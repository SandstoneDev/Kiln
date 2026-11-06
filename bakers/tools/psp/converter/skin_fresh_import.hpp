// PSP: import a fresh converted skin while retaining pack texture and attachment bindings.
#pragma once
#include "rendering/psp/psp_skeleton.hpp"
#include <limits>
#include <map>
#include <set>
#include <stdexcept>
#include <utility>
#include <vector>

namespace wowee::pspconv {

// PSP: the fresh converter owns geometry and animation, but its local texture
// numbers belong to another pack. Preserve existing p_ attachment bone indices
// by permuting fresh bones/tracks; retain every byte outside those bindings.
// Nothing is relocated, including extension headers, timed keys and events.
inline std::vector<uint8_t> importFreshSkin(const std::vector<uint8_t>& oldBlob,
                                          const std::vector<uint8_t>& freshBlob) {
    namespace f = rendering::psp;
    if (oldBlob.size() > std::numeric_limits<uint32_t>::max() ||
        freshBlob.size() > std::numeric_limits<uint32_t>::max())
        throw std::runtime_error("skin blob exceeds 32-bit format bounds");
    f::SkinView old, fresh;
    if (!old.init(oldBlob.data(), static_cast<uint32_t>(oldBlob.size())) ||
        !fresh.init(freshBlob.data(), static_cast<uint32_t>(freshBlob.size())))
        throw std::runtime_error("invalid old or fresh skin");
    if (old.h->batchCount != fresh.h->batchCount)
        throw std::runtime_error("fresh skin batch count differs");
    auto lodSplit = [](const f::SkinHeader& h) {
        if (!(h.flags & f::kSkinHasLod)) return static_cast<uint32_t>(h.batchCount);
        if (!h.reserved[0] || h.reserved[0] > h.batchCount)
            throw std::runtime_error("invalid skin LOD split");
        return h.reserved[0];
    };
    if (lodSplit(*old.h) != lodSplit(*fresh.h))
        throw std::runtime_error("fresh skin LOD split differs");
    for (unsigned b = 0; b < old.h->batchCount; ++b) {
        if (old.geosets[b] != fresh.geosets[b])
            throw std::runtime_error("fresh skin geoset order differs");
        if (old.batches[b].blend != fresh.batches[b].blend ||
            old.batches[b].flags != fresh.batches[b].flags)
            throw std::runtime_error("fresh skin material differs");
    }

    const unsigned count = fresh.h->boneCount;
    std::map<uint16_t, unsigned> freshBySource;
    for (unsigned b = 0; b < count; ++b)
        if (!freshBySource.emplace(fresh.bones[b].m2Bone, b).second)
            throw std::runtime_error("fresh skin has duplicate source bone IDs");
    std::set<uint16_t> oldSourceIds;
    std::vector<unsigned> newToFresh;
    std::vector<int> freshToNew(count, -1);
    auto sameParent = [&](const f::SkinBone& a, const f::SkinBone& b) {
        if (a.parent < 0 || b.parent < 0) return a.parent == b.parent;
        return old.bones[a.parent].m2Bone == fresh.bones[b.parent].m2Bone;
    };
    for (unsigned b = 0; b < old.h->boneCount; ++b) {
        const auto& prior = old.bones[b];
        if (!oldSourceIds.insert(prior.m2Bone).second)
            throw std::runtime_error("old skin has duplicate source bone IDs");
        const auto found = freshBySource.find(prior.m2Bone);
        if (found == freshBySource.end())
            throw std::runtime_error("fresh skin omits an existing attachment bone");
        const auto& replacement = fresh.bones[found->second];
        if (!sameParent(prior, replacement))
            throw std::runtime_error("fresh skin source parent relation differs");
        for (unsigned a = 0; a < 3; ++a)
            if (!std::isfinite(prior.pivot[a]) || !std::isfinite(replacement.pivot[a]) ||
                prior.pivot[a] != replacement.pivot[a])
                throw std::runtime_error("fresh skin source pivot differs");
        freshToNew[found->second] = static_cast<int>(newToFresh.size());
        newToFresh.push_back(found->second);
    }
    // Fresh SkinView already guarantees parent-before-child. Once old bones
    // occupy the prefix, appending remaining bones in fresh order stays sorted.
    for (unsigned b = 0; b < count; ++b) {
        if (freshToNew[b] >= 0) continue;
        freshToNew[b] = static_cast<int>(newToFresh.size());
        newToFresh.push_back(b);
    }
    std::set<std::pair<uint16_t, uint16_t>> freshSequences;
    for (unsigned s = 0; s < fresh.h->sequenceCount; ++s)
        freshSequences.emplace(fresh.sequences[s].animId, fresh.sequences[s].variation);
    for (unsigned s = 0; s < old.h->sequenceCount; ++s)
        if (!freshSequences.count({old.sequences[s].animId, old.sequences[s].variation}))
            throw std::runtime_error("fresh skin omits an existing sequence");

    auto result = freshBlob;
    auto store = [&](size_t offset, const auto& value) {
        std::memcpy(result.data() + offset, &value, sizeof(value));
    };
    for (unsigned b = 0; b < count; ++b) {
        const unsigned from = newToFresh[b];
        auto bone = fresh.bones[from];
        if (bone.parent >= 0) bone.parent = static_cast<int16_t>(freshToNew[bone.parent]);
        if (bone.parent >= static_cast<int>(b))
            throw std::runtime_error("reordered fresh skeleton is not parent-before-child");
        store(fresh.h->boneOffset + b * sizeof(bone), bone);
        for (unsigned s = 0; s < fresh.h->sequenceCount; ++s)
            store(fresh.h->trackOffset + (static_cast<size_t>(s) * count + b) * sizeof(f::BoneTrack),
                  fresh.tracks[static_cast<size_t>(s) * count + from]);
    }
    for (unsigned b = 0; b < fresh.h->batchCount; ++b) {
        auto batch = fresh.batches[b];
        batch.texture = old.batches[b].texture;
        store(fresh.h->batchOffset + b * sizeof(batch), batch);
    }
    for (unsigned b = 0; b < fresh.h->subBatchCount; ++b) {
        auto sub = fresh.subBatches[b];
        if (sub.reserved & f::kSkinVirtualPalette) {
            for (unsigned slot = 0; slot < sub.weights; ++slot) {
                auto blend = *fresh.paletteBlend(fresh.subBatches[b], slot);
                for (unsigned k = 0; k < 4; ++k)
                    if (blend.weights[k]) blend.bones[k] = static_cast<uint8_t>(freshToNew[blend.bones[k]]);
                store(fresh.h->reserved[2] + sizeof(f::SkinPaletteHeader) +
                          (static_cast<size_t>(b) * f::kSkinMaxPalette + slot) * sizeof(blend), blend);
            }
        } else {
            for (unsigned slot = 0; slot < sub.weights; ++slot)
                sub.palette[slot] = static_cast<uint8_t>(freshToNew[sub.palette[slot]]);
            store(fresh.h->subBatchOffset + b * sizeof(sub), sub);
        }
    }
    f::SkinView checked;
    if (!checked.init(result.data(), static_cast<uint32_t>(result.size())))
        throw std::runtime_error("invalid imported skin");
    return result;
}

} // namespace wowee::pspconv
