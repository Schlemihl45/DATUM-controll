/**
 * voxel_mod.cpp — nanobind Python extension for OpenVDB-based voxel operations.
 *
 * Exposed Python API:
 *
 *   GridHandle  init_stock(bbox: tuple[float,6], voxel_size: float) -> GridHandle
 *       Initialise a level-set FloatGrid representing a solid box.
 *
 *   void  subtract_segment(grid: GridHandle,
 *                           start: tuple[float,3], end: tuple[float,3],
 *                           tool_profile: ToolProfile)
 *       CSG-subtract the swept tool volume from *grid* for one path segment.
 *       Uses point-sampling along the path at ≤ voxel_size/2 intervals, with
 *       z-sliced profile_radius_at() disks sampled along the tool axis.
 *
 *   tuple  get_mesh(grid: GridHandle) -> (vertices_f32, normals_f32, indices_u32)
 *       Mesh the level-set surface using volumeToMesh and return three numpy
 *       arrays ready for GPU upload:
 *         vertices  — (N, 3) float32
 *         normals   — (N, 3) float32  (face normals, duplicated per vertex)
 *         indices   — (M, 3) uint32
 *
 * Python/C++ boundary:
 *   Python holds only opaque GridHandle objects — no voxel logic lives in Python.
 *   ToolProfile is constructed on the Python side and passed by value.
 *
 * Build:
 *   See CMakeLists.txt in this directory.
 *   Dependencies: OpenVDB ≥ 10.0, TBB, Blosc (optional), nanobind ≥ 2.0.
 */

#include <nanobind/nanobind.h>
#include <nanobind/ndarray.h>
#include <nanobind/stl/tuple.h>
#include <nanobind/stl/array.h>

#include <openvdb/openvdb.h>
#include <openvdb/tools/LevelSetSphere.h>
#include <openvdb/tools/LevelSetUtil.h>
#include <openvdb/tools/Composite.h>
#include <openvdb/tools/VolumeToMesh.h>
#include <openvdb/math/Transform.h>

#include <cmath>
#include <vector>
#include <array>
#include <memory>
#include <stdexcept>
#include <algorithm>

namespace nb = nanobind;
using namespace nb::literals;

// ── Tool type enum ────────────────────────────────────────────────────────────

enum class ToolType : int {
    ENDMILL      = 0,
    BALL_ENDMILL = 1,
    BULL_ENDMILL = 2,
    CHAMFER      = 3,
    DRILL        = 4,
    TAPER        = 5,
};

// ── ToolProfile (mirrors Python ToolDefinition fields) ────────────────────────

struct ToolProfile {
    ToolType tool_type      = ToolType::ENDMILL;
    float    diameter       = 6.0f;    // mm
    float    corner_radius  = 0.0f;    // mm (bull-endmill corner radius)
    float    tip_angle      = 118.0f;  // degrees (drill / chamfer included angle)
    float    taper_angle    = 3.0f;    // degrees (taper mill half-angle)
    float    cutting_length = 20.0f;   // mm (how deep the tool cuts)

    float radius() const { return diameter * 0.5f; }
};

// ── profile_radius_at: mirrors Python ToolDefinition.profile_radius_at(z) ────

static float profile_radius_at(const ToolProfile& t, float z) {
    if (z < 0.0f) return 0.0f;
    const float r = t.radius();

    switch (t.tool_type) {
        case ToolType::ENDMILL:
            return r;

        case ToolType::BALL_ENDMILL:
            if (z <= r) {
                float val = r * r - (r - z) * (r - z);
                return val > 0.0f ? std::sqrt(val) : 0.0f;
            }
            return r;

        case ToolType::BULL_ENDMILL: {
            float cr    = std::min(t.corner_radius, r);
            float flat_r = r - cr;
            if (z <= cr) {
                float val = cr * cr - (cr - z) * (cr - z);
                return flat_r + (val > 0.0f ? std::sqrt(val) : 0.0f);
            }
            return r;
        }

        case ToolType::CHAMFER: {
            float half = t.tip_angle * 0.5f * (float)M_PI / 180.0f;
            return std::min(z * std::tan(half), r);
        }

        case ToolType::DRILL: {
            float half   = t.tip_angle * 0.5f * (float)M_PI / 180.0f;
            float tip_h  = r / std::tan(half);
            return z <= tip_h ? z * std::tan(half) : r;
        }

        case ToolType::TAPER: {
            float tip_r = 0.5f;
            float ang   = t.taper_angle * (float)M_PI / 180.0f;
            return std::min(tip_r + z * std::tan(ang), r);
        }
    }
    return r;
}

// ── GridHandle — opaque wrapper for Python ────────────────────────────────────

struct GridHandle {
    openvdb::FloatGrid::Ptr grid;
    float voxel_size;  // cached for use in subtract_segment
};

// ── Initialise stock from bounding box ───────────────────────────────────────

/**
 * Create a solid box level-set in the range [min_x,max_x] × [min_y,max_y] × [min_z,max_z].
 *
 * The level-set stores negative values inside the solid (material present) and
 * positive values outside. CSG difference removes material from the inside.
 *
 * @param bbox_tuple  Flat 6-tuple: (min_x, min_y, min_z, max_x, max_y, max_z) in mm.
 * @param voxel_size  Edge length of one voxel in mm.
 */
static GridHandle init_stock(std::array<float, 6> bbox_tuple, float voxel_size) {
    openvdb::initialize();

    if (voxel_size <= 0.0f)
        throw std::invalid_argument("voxel_size must be positive");

    float min_x = bbox_tuple[0], min_y = bbox_tuple[1], min_z = bbox_tuple[2];
    float max_x = bbox_tuple[3], max_y = bbox_tuple[4], max_z = bbox_tuple[5];

    if (min_x >= max_x || min_y >= max_y || min_z >= max_z)
        throw std::invalid_argument("bbox: max must be > min on every axis");

    // Build a half-size box SDF: negative inside, positive outside.
    // Strategy: construct as the intersection (union of SDFs) of 6 half-spaces.
    auto xform = openvdb::math::Transform::createLinearTransform(voxel_size);
    auto grid  = openvdb::FloatGrid::create(/*background=*/3.0f * voxel_size);
    grid->setTransform(xform);
    grid->setGridClass(openvdb::GRID_LEVEL_SET);

    auto accessor = grid->getAccessor();

    // Voxel-space iteration bounds (add narrow-band padding of 3 voxels)
    int band = 3;
    int ix0 = (int)std::floor(min_x / voxel_size) - band;
    int iy0 = (int)std::floor(min_y / voxel_size) - band;
    int iz0 = (int)std::floor(min_z / voxel_size) - band;
    int ix1 = (int)std::ceil(max_x  / voxel_size) + band;
    int iy1 = (int)std::ceil(max_y  / voxel_size) + band;
    int iz1 = (int)std::ceil(max_z  / voxel_size) + band;

    float bg = 3.0f * voxel_size;

    for (int iz = iz0; iz <= iz1; ++iz) {
        for (int iy = iy0; iy <= iy1; ++iy) {
            for (int ix = ix0; ix <= ix1; ++ix) {
                float wx = ix * voxel_size;
                float wy = iy * voxel_size;
                float wz = iz * voxel_size;

                // SDF of a box: negative = inside, positive = outside.
                // Uses the standard "max of signed distances to each face" formulation.
                float dx = std::max(min_x - wx, wx - max_x);
                float dy = std::max(min_y - wy, wy - max_y);
                float dz = std::max(min_z - wz, wz - max_z);
                float sdf = std::max({dx, dy, dz});

                if (sdf < bg)
                    accessor.setValue(openvdb::Coord(ix, iy, iz), sdf);
            }
        }
    }

    grid->pruneGrid();
    return GridHandle{grid, voxel_size};
}

// ── Subtract one path segment from the stock ─────────────────────────────────

/**
 * CSG-subtract the swept tool volume for one motion segment from *handle*.
 *
 * Algorithm:
 *   1. Sample positions along [start → end] at ≤ voxel_size/2 spacing.
 *   2. At each sample point, iterate along the tool's Z axis (tip downward)
 *      and create sphere-of-radius profile_radius_at(z) at position
 *      (sample_x, sample_y, sample_z − z).
 *   3. CSG-union all these spheres into a temporary volume.
 *   4. CSG-difference the temporary volume from the stock grid.
 *
 * @param handle  GridHandle returned by init_stock().
 * @param start   Tool tip start position [x, y, z] in mm.
 * @param end     Tool tip end   position [x, y, z] in mm.
 * @param tool    ToolProfile describing tool geometry.
 */
static void subtract_segment(GridHandle& handle,
                              std::array<float, 3> start,
                              std::array<float, 3> end,
                              const ToolProfile& tool)
{
    if (!handle.grid)
        throw std::runtime_error("subtract_segment: null grid handle");

    float voxel_size = handle.voxel_size;

    // Path sampling: at most every voxel_size/2 mm
    float dx = end[0] - start[0];
    float dy = end[1] - start[1];
    float dz = end[2] - start[2];
    float path_len = std::sqrt(dx*dx + dy*dy + dz*dz);

    int n_path = std::max(1, (int)std::ceil(path_len / (voxel_size * 0.5f)));

    // Tool Z-slice sampling: every voxel_size mm along the tool axis
    float cutting_depth = tool.cutting_length > 0.0f ? tool.cutting_length : 50.0f;
    int n_z = std::max(1, (int)std::ceil(cutting_depth / voxel_size));

    // Build a temporary level-set that is the union of all tool positions
    auto tmp = openvdb::FloatGrid::create(3.0f * voxel_size);
    tmp->setTransform(handle.grid->transformPtr());
    tmp->setGridClass(openvdb::GRID_LEVEL_SET);
    bool tmp_has_data = false;

    for (int i = 0; i <= n_path; ++i) {
        float t  = (n_path > 0) ? (float)i / n_path : 0.0f;
        float cx = start[0] + t * dx;
        float cy = start[1] + t * dy;
        float cz = start[2] + t * dz;

        for (int j = 0; j <= n_z; ++j) {
            float tool_z = (float)j * cutting_depth / n_z;
            float r = profile_radius_at(tool, tool_z);
            if (r < 1e-5f) continue;

            // Centre of the sphere approximating one z-slice disk
            openvdb::Vec3f center(cx, cy, cz - tool_z);

            auto sphere = openvdb::tools::createLevelSetSphere<openvdb::FloatGrid>(
                r, center, voxel_size, /*half_width=*/3.0f
            );

            if (!tmp_has_data) {
                openvdb::tools::csgUnion(*tmp, *sphere);
                tmp_has_data = true;
            } else {
                openvdb::tools::csgUnion(*tmp, *sphere);
            }
        }
    }

    if (tmp_has_data)
        openvdb::tools::csgDifference(*handle.grid, *tmp);
}

// ── Mesh extraction ───────────────────────────────────────────────────────────

/**
 * Extract a triangle mesh from the stock level-set surface.
 *
 * Uses openvdb::tools::volumeToMesh, then:
 *   - Quads are split into two triangles.
 *   - Per-face normals are computed and replicated to per-vertex normals.
 *
 * Returns three numpy arrays (vertices f32, normals f32, indices u32).
 */
static auto get_mesh(const GridHandle& handle)
{
    if (!handle.grid)
        throw std::runtime_error("get_mesh: null grid handle");

    std::vector<openvdb::Vec3s>  vdb_points;
    std::vector<openvdb::Vec3I>  vdb_triangles;
    std::vector<openvdb::Vec4I>  vdb_quads;

    openvdb::tools::volumeToMesh(
        *handle.grid, vdb_points, vdb_triangles, vdb_quads,
        /*isovalue=*/0.0, /*adaptivity=*/0.0
    );

    // Collect all triangles (quads split into 2)
    std::vector<std::array<uint32_t, 3>> tris;
    tris.reserve(vdb_triangles.size() + vdb_quads.size() * 2);

    for (auto& tri : vdb_triangles)
        tris.push_back({(uint32_t)tri[0], (uint32_t)tri[1], (uint32_t)tri[2]});

    for (auto& q : vdb_quads) {
        tris.push_back({(uint32_t)q[0], (uint32_t)q[1], (uint32_t)q[2]});
        tris.push_back({(uint32_t)q[0], (uint32_t)q[2], (uint32_t)q[3]});
    }

    size_t n_pts  = vdb_points.size();
    size_t n_tris = tris.size();

    // Build flat vertex array and accumulate normals per vertex
    std::vector<float>    vertices(n_pts * 3);
    std::vector<float>    normals(n_pts * 3, 0.0f);
    std::vector<uint32_t> indices(n_tris * 3);

    for (size_t i = 0; i < n_pts; ++i) {
        vertices[i*3+0] = vdb_points[i][0];
        vertices[i*3+1] = vdb_points[i][1];
        vertices[i*3+2] = vdb_points[i][2];
    }

    for (size_t i = 0; i < n_tris; ++i) {
        uint32_t a = tris[i][0], b = tris[i][1], c = tris[i][2];
        indices[i*3+0] = a;
        indices[i*3+1] = b;
        indices[i*3+2] = c;

        // Face normal via cross product
        float ax = vertices[b*3]-vertices[a*3], ay = vertices[b*3+1]-vertices[a*3+1], az = vertices[b*3+2]-vertices[a*3+2];
        float bx = vertices[c*3]-vertices[a*3], by = vertices[c*3+1]-vertices[a*3+1], bz = vertices[c*3+2]-vertices[a*3+2];
        float nx = ay*bz - az*by;
        float ny = az*bx - ax*bz;
        float nz = ax*by - ay*bx;

        // Accumulate into each vertex (will normalize at the end)
        for (uint32_t vi : {a, b, c}) {
            normals[vi*3+0] += nx;
            normals[vi*3+1] += ny;
            normals[vi*3+2] += nz;
        }
    }

    // Normalize accumulated normals
    for (size_t i = 0; i < n_pts; ++i) {
        float nx = normals[i*3+0], ny = normals[i*3+1], nz = normals[i*3+2];
        float len = std::sqrt(nx*nx + ny*ny + nz*nz);
        if (len > 1e-8f) {
            normals[i*3+0] /= len;
            normals[i*3+1] /= len;
            normals[i*3+2] /= len;
        }
    }

    // Wrap in numpy arrays (nanobind copies the data into numpy-owned memory)
    size_t vshape[2] = {n_pts,  3};
    size_t ishape[2] = {n_tris, 3};

    auto np_vertices = nb::ndarray<nb::numpy, float,    nb::shape<nb::any, 3>>(
        vertices.data(), 2, vshape
    );
    auto np_normals = nb::ndarray<nb::numpy, float,    nb::shape<nb::any, 3>>(
        normals.data(), 2, vshape
    );
    auto np_indices = nb::ndarray<nb::numpy, uint32_t, nb::shape<nb::any, 3>>(
        indices.data(), 2, ishape
    );

    return std::make_tuple(np_vertices, np_normals, np_indices);
}

// ── nanobind module definition ────────────────────────────────────────────────

NB_MODULE(voxel_mod, m) {
    m.doc() = "OpenVDB-based voxel material-removal operations for datum_sim";

    // ToolType enum
    nb::enum_<ToolType>(m, "ToolType")
        .value("ENDMILL",      ToolType::ENDMILL)
        .value("BALL_ENDMILL", ToolType::BALL_ENDMILL)
        .value("BULL_ENDMILL", ToolType::BULL_ENDMILL)
        .value("CHAMFER",      ToolType::CHAMFER)
        .value("DRILL",        ToolType::DRILL)
        .value("TAPER",        ToolType::TAPER)
        .export_values();

    // ToolProfile
    nb::class_<ToolProfile>(m, "ToolProfile")
        .def(nb::init<>())
        .def_rw("tool_type",      &ToolProfile::tool_type)
        .def_rw("diameter",       &ToolProfile::diameter)
        .def_rw("corner_radius",  &ToolProfile::corner_radius)
        .def_rw("tip_angle",      &ToolProfile::tip_angle)
        .def_rw("taper_angle",    &ToolProfile::taper_angle)
        .def_rw("cutting_length", &ToolProfile::cutting_length);

    // GridHandle — opaque Python object
    nb::class_<GridHandle>(m, "GridHandle")
        .def_prop_ro("voxel_size", [](const GridHandle& h){ return h.voxel_size; });

    // Functions
    m.def("init_stock",        &init_stock,
          "bbox"_a, "voxel_size"_a,
          "Initialise a solid bounding-box level-set. "
          "bbox: (min_x, min_y, min_z, max_x, max_y, max_z) in mm.");

    m.def("subtract_segment",  &subtract_segment,
          "grid"_a, "start"_a, "end"_a, "tool"_a,
          "CSG-subtract the swept tool volume for one motion segment.");

    m.def("get_mesh",          &get_mesh,
          "grid"_a,
          "Mesh the stock surface. Returns (vertices_f32, normals_f32, indices_u32).");
}
