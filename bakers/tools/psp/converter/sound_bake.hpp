// Sounds - the terrain half of psp_sound_conv (footstep surface
// grids, include/rendering/psp/psp_sound_format.hpp WSRF), in a header so the
// host test (tools/psp/host_tests/sound_test.cpp) runs the same code.
#pragma once

#include "pipeline/adt_alpha.hpp"
#include "pipeline/adt_loader.hpp"
#include "rendering/psp/psp_sound_format.hpp"
#include "rendering/water_mask.hpp"

#include <algorithm>
#include <cctype>
#include <cstring>
#include <initializer_list>
#include <string>
#include <vector>

namespace wowee::pspconv {

namespace sfmt = wowee::rendering::psp;

// upstream FootstepDriver::resolveFootstepSurface: tokens of the lowercased
// texture path, in its order; no texture = STONE.
inline uint8_t surfaceOfTexture(std::string t) {
    for (char& ch : t) ch = static_cast<char>(std::tolower(static_cast<unsigned char>(ch)));
    auto any = [&](std::initializer_list<const char*> toks) {
        for (const char* k : toks)
            if (t.find(k) != std::string::npos) return true;
        return false;
    };
    if (any({"snow", "ice"})) return sfmt::kSurfSnow;
    if (any({"farm", "field", "soil", "plow", "crop", "wheat", "dirt", "mud", "sand"})) return sfmt::kSurfDirt;
    if (any({"grass", "moss", "leaf"})) return sfmt::kSurfGrass;
    if (any({"wood", "timber"})) return sfmt::kSurfWood;
    if (any({"metal", "iron"})) return sfmt::kSurfMetal;
    return sfmt::kSurfStone;  // "stone", "rock", "cobble", "brick" and everything else
}

// One ADT tile -> WSRF grid (psp_sound_format.hpp).
inline std::vector<uint8_t> surfaceGrid(const pipeline::ADTTerrain& t, uint32_t hist[8], uint32_t& waterCells) {
    std::vector<uint8_t> out(sfmt::kSurfaceGridBytes, 0);
    sfmt::SurfaceGridHeader h{};
    std::memcpy(h.magic, "WSRF", 4);
    h.version = sfmt::kSoundFormatVersion;
    std::memcpy(out.data(), &h, sizeof(h));
    uint8_t* cells = out.data() + sizeof(h);
    auto* water = reinterpret_cast<float*>(cells + 256 * 32);
    std::vector<uint8_t> alpha;
    for (int c = 0; c < 256; ++c) {
        const pipeline::MapChunk& ch = t.chunks[c];
        // Liquid cells and the highest surface of the chunk.
        bool wet[64] = {};
        float wmax = 0.0f;
        bool anyWater = false;
        for (const auto& layer : t.waterData[c].layers) {
            for (int ly = 0; ly < layer.height; ++ly)
                for (int lx = 0; lx < layer.width; ++lx) {
                    const int col = layer.x + lx, row = layer.y + ly;
                    if (col > 7 || row > 7) continue;
                    // MH2O x = grid column (world Y), y = grid row (world X): upstream water_renderer.
                    if (!rendering::waterCellRendered(layer.mask, 0, layer.width, layer.x, layer.y, lx, ly)) continue;
                    wet[row * 8 + col] = true;
                }
            wmax = anyWater ? std::max(wmax, layer.maxHeight) : layer.maxHeight;
            anyWater = true;
        }
        water[c] = anyWater ? wmax : 0.0f;
        const size_t nl = std::min<size_t>(ch.layers.size(), 4);
        for (int row = 0; row < 8; ++row) {
            for (int col = 0; col < 8; ++col) {
                // upstream getDominantTextureAt at the cell centre: fracY = row
                // + 0.5 (down world X), fracX = col + 0.5 (down world Y).
                uint8_t s = sfmt::kSurfStone;
                if (nl > 0) {
                    const float fracX = static_cast<float>(col) + 0.5f, fracY = static_cast<float>(row) + 0.5f;
                    const size_t ai = pipeline::alphaTexelIndex(fracX / 8.0f, fracY / 8.0f);
                    int weights[4] = {0, 0, 0, 0};
                    int accum = 0;
                    for (size_t l = 1; l < nl; ++l) {
                        int a = 0;
                        if (pipeline::decodeLayerAlpha(ch, l, alpha) && ai < alpha.size()) a = alpha[ai];
                        weights[l] = a;
                        accum += a;
                    }
                    weights[0] = std::clamp(255 - accum, 0, 255);
                    size_t best = 0;
                    for (size_t l = 1; l < nl; ++l)
                        if (weights[l] > weights[best]) best = l;
                    const uint32_t texId = ch.layers[best].textureId;
                    if (texId < t.textures.size()) s = surfaceOfTexture(t.textures[texId]);
                }
                ++hist[s];
                uint8_t nib = s;
                if (wet[row * 8 + col]) {
                    nib |= sfmt::kSurfCellWater;
                    ++waterCells;
                }
                const int n = c * 64 + row * 8 + col;
                cells[n / 2] |= static_cast<uint8_t>(nib << ((n & 1) * 4));
            }
        }
    }
    return out;
}

} // namespace wowee::pspconv
