// Character attachment points for the PSP ("p_<race>_<sex>" WATP,
// include/rendering/psp/psp_model_format.hpp AttachPoint), used by
// psp_char_conv.
//
// Upstream CharacterRenderer::findAttachmentBone resolves an attachment id to
// a bone and an offset (lookup table, then scan by id, then key bones 26/27
// for the hands, bone 0 for the helm); the item sits at
// boneMatrix * T(offset). The bones of the points the equipment rules use
// are kept in the WSKN bone list (bakeSkinned keepBones) even when no vertex
// is weighted to them, so the PSP poses them with the rest.
#pragma once

#include "rendering/psp/psp_skeleton.hpp"

#include "skin_bake.hpp"

#include <cmath>
#include <cstring>
#include <set>
#include <string>

namespace wowee::pspconv {

// Attachment ids the PSP equipment rules place items on (psp_equipment.cpp,
// upstream appearance_composer / entity_spawner_player): shield 0, hands 1/2,
// shoulders 5/6, helm 11, back 12, sheathed shield 28, hips 32/33.
// Also the other sheath points (equipmentAttachmentsBySheath: two-handers
// 26 / 27, staves 30 / 31) - their bones carry no vertices, so without this "n_" dropped them and the PSP drew a
// sheathed two-hander on the Stand frame's matrix (not following the back).
inline const std::vector<uint32_t>& attachIdsUsed() {
    static const std::vector<uint32_t> ids = {0, 1, 2, 5, 6, 11, 12, 26, 27, 28, 30, 31, 32, 33};
    return ids;
}

// upstream CharacterRenderer::findAttachmentBone, unchanged.
inline bool findAttachmentBone(const pipeline::M2Model& model, uint32_t attachmentId, uint16_t& outBoneIndex,
                               glm::vec3& outOffset) {
    outBoneIndex = 0;
    outOffset = glm::vec3(0.0f);
    bool found = false;

    // Try attachment lookup first
    if (attachmentId < model.attachmentLookup.size()) {
        uint16_t attIdx = model.attachmentLookup[attachmentId];
        if (attIdx < model.attachments.size()) {
            outBoneIndex = model.attachments[attIdx].bone;
            outOffset = model.attachments[attIdx].position;
            found = true;
        }
    }

    // Fallback: scan attachments by id
    if (!found) {
        for (const auto& att : model.attachments) {
            if (att.id == attachmentId) {
                outBoneIndex = att.bone;
                outOffset = att.position;
                found = true;
                break;
            }
        }
    }

    // Fallback: key-bone lookup for weapon hand attachment IDs (ID 1 = right hand, ID 2 = left hand)
    if (!found && (attachmentId == 1 || attachmentId == 2)) {
        int32_t targetKeyBone = (attachmentId == 1) ? 26 : 27;
        for (size_t i = 0; i < model.bones.size(); i++) {
            if (model.bones[i].keyBoneId == targetKeyBone) {
                outBoneIndex = static_cast<uint16_t>(i);
                outOffset = glm::vec3(0.0f);
                found = true;
                break;
            }
        }
    }

    // Fallback for head attachment (ID 11): use bone 0 if attachment not defined
    if (!found && attachmentId == 11 && !model.bones.empty()) {
        outBoneIndex = 0;
        found = true;
    }

    // Validate bone index
    if (found && outBoneIndex >= model.bones.size()) {
        found = false;
    }

    return found;
}

// Every attachment id the model can resolve: its own ids, the lookup table's
// range and the upstream fallbacks (1, 2, 11).
inline std::set<uint32_t> attachIdsOf(const pipeline::M2Model& model) {
    std::set<uint32_t> ids = {1, 2, 11};
    for (const auto& a : model.attachments) ids.insert(a.id);
    for (uint32_t i = 0; i < model.attachmentLookup.size(); ++i) ids.insert(i);
    for (uint32_t id : attachIdsUsed()) ids.insert(id);
    return ids;
}

// PSP: quality-only copy with every resolved point, including upstream fallbacks.
// Resolve against the untouched original: appending to the attachment array must
// never turn an originally invalid attachmentLookup index into a valid one.
inline pipeline::M2Model attachmentWitnessModel(const pipeline::M2Model& model) {
    auto quality=model;
    for(uint32_t id:attachIdsOf(model)) {
        uint16_t bone=0;glm::vec3 position;
        if(!findAttachmentBone(model,id,bone,position))continue;
        if(std::none_of(quality.attachments.begin(),quality.attachments.end(),[&](const auto& existing) {
            return existing.bone==bone&&existing.position==position;
        }))quality.attachments.push_back(pipeline::M2Attachment{id,bone,position});
    }
    return quality;
}

// Bones of the points the equipment rules use: kept in "n_" (keepBones).
inline std::vector<uint16_t> attachmentKeepBones(const pipeline::M2Model& model) {
    std::vector<uint16_t> keep;
    for (uint32_t id : attachIdsUsed()) {
        uint16_t bone = 0;
        glm::vec3 off;
        if (findAttachmentBone(model, id, bone, off)) keep.push_back(bone);
    }
    return keep;
}

// PSP: the scoped proof covers every emitted point, including vehicle seats.
// Retain its real source bone instead of silently freezing a moving point.
inline std::vector<uint16_t> creatureAttachmentKeepBones(const pipeline::M2Model& model,bool vehiclePoints) {
    std::vector<uint16_t> keep;
    std::vector<uint16_t> ids(std::begin(fmt::kCreaturePointIds),std::end(fmt::kCreaturePointIds));
    if(vehiclePoints)ids.insert(ids.end(),std::begin(fmt::kVehicleSeatPointIds),std::end(fmt::kVehicleSeatPointIds));
    for(uint16_t id:ids) {
        uint16_t bone=0;glm::vec3 offset;
        if(findAttachmentBone(model,id,bone,offset))keep.push_back(bone);
    }
    return keep;
}

struct AttachBakeInfo {
    size_t points = 0, inSkin = 0;
    float maxErr = 0.0f;       // --verify: PSP pose vs upstream pose, attachment position (yards)
    float maxAxisErr = 0.0f;   // --verify: largest difference of a rotated unit axis
    size_t samples = 0;
    size_t staticSamples = 0; // PSP: proves scoped static points were checked, not skipped
    std::string worst;
};

// The WATP blob. standSeq: M2 sequence of Stand (its keys loaded), -1 = bind
// pose (identity). nBlob: the baked "n_" (null: static model only).
// Mounts - onlyIds (psp_creature_conv --mounts): just these ids
// (the seat probe of upstream applyMountPositioning) instead of every id the
// model resolves; extra: points appended as they are (kAttachRiderHeight).
inline std::vector<uint8_t> bakeAttachTable(const pipeline::M2Model& model, int standSeq,
                                           const std::vector<uint8_t>* nBlob, bool verify, AttachBakeInfo& info,
                                           const std::vector<uint32_t>* onlyIds = nullptr,
                                           const std::vector<fmt::AttachPoint>* extra = nullptr,
                                           const SkinVisibilityPolicy* visibility = nullptr,
                                           const std::vector<std::set<float>>* verificationTimes = nullptr) {
    info = AttachBakeInfo{};
    std::vector<glm::mat4> stand;
    if (standSeq >= 0) poseBones(model, standSeq, 0.0f, stand);
    fmt::SkinView view;
    const bool skinned = nBlob && view.init(nBlob->data(), static_cast<uint32_t>(nBlob->size()));
    std::vector<fmt::AttachPoint> points;
    std::set<uint32_t> ids = attachIdsOf(model);
    if (onlyIds) ids = std::set<uint32_t>(onlyIds->begin(), onlyIds->end());
    for (uint32_t id : ids) {
        uint16_t bone = 0;
        glm::vec3 off;
        if (!findAttachmentBone(model, id, bone, off)) continue;
        fmt::AttachPoint p{};
        p.id = static_cast<uint16_t>(id);
        p.bone = fmt::kNoAttachBone;
        if (skinned)
            for (uint16_t i = 0; i < view.h->boneCount; ++i)
                if (view.bones[i].m2Bone == bone) p.bone = i;
        for (int a = 0; a < 3; ++a) p.position[a] = off[a];
        const glm::mat4 s = stand.empty() ? glm::mat4(1.0f) : stand[bone];
        for (int c = 0; c < 4; ++c)
            for (int r = 0; r < 3; ++r) p.stand[c * 3 + r] = s[c][r];
        points.push_back(p);
        if (p.bone != fmt::kNoAttachBone) ++info.inSkin;
    }
    info.points = points.size();

    // --verify: the PSP's pose (psp_skeleton.hpp evaluatePose on the baked
    // keys) against upstream's (poseBones, m2_track) at each point, for every
    // sequence of "n_" at source keys and baked-frame interval quarters.
    if (verify && skinned) {
        std::vector<fmt::BoneMatrix> pose(view.h->boneCount);
        std::vector<glm::mat4> ref;
        for (int s = 0; s < view.h->sequenceCount; ++s) {
            const fmt::AnimSequence& as = view.sequences[s];
            int m2seq = -1;
            for (size_t i = 0; i < model.sequences.size(); ++i)
                if (model.sequences[i].id == as.animId && model.sequences[i].variationIndex == as.variation)
                    m2seq = static_cast<int>(i);
            if (m2seq < 0) continue;
            const auto* pins=verificationTimes&&static_cast<size_t>(s)<verificationTimes->size()?&(*verificationTimes)[s]:nullptr;
            for (float t : skinProbeTimes(view,static_cast<uint32_t>(s),model,m2seq,true,pins,visibility)) {
                fmt::evaluatePose(view, s, t, pose.data());
                poseBones(model, m2seq, t, ref);
                for (const auto& p : points) {
                    if (p.bone == fmt::kNoAttachBone && !visibility) continue;
                    uint16_t sourceBone=0;glm::vec3 sourceOffset;
                    if(!findAttachmentBone(model,p.id,sourceBone,sourceOffset)||sourceBone>=ref.size()) {
                        if(visibility){info.maxErr=std::numeric_limits<float>::infinity();info.worst="unresolved source point";}
                        continue;
                    }
                    const float* matrix=p.bone==fmt::kNoAttachBone?p.stand:pose[p.bone].m;
                    const glm::mat4& u = ref[sourceBone];
                    const glm::vec3 pos(p.position[0], p.position[1], p.position[2]);
                    glm::vec3 psp;
                    for (int r = 0; r < 3; ++r)
                        psp[r] = matrix[r] * pos.x + matrix[3 + r] * pos.y + matrix[6 + r] * pos.z + matrix[9 + r];
                    const glm::vec3 up = glm::vec3(u * glm::vec4(sourceOffset, 1.0f));
                    const float err = glm::length(psp - up);
                    float axisErr = 0.0f;
                    for (int c = 0; c < 3; ++c)
                        axisErr = std::max(axisErr, glm::length(glm::vec3(matrix[c * 3], matrix[c * 3 + 1], matrix[c * 3 + 2]) -
                                                                glm::vec3(u[c])));
                    ++info.samples;
                    if(p.bone==fmt::kNoAttachBone)++info.staticSamples;
                    info.maxAxisErr = std::max(info.maxAxisErr, axisErr);
                    if (err > info.maxErr || (visibility&&!std::isfinite(err))) {
                        info.maxErr = std::isfinite(err)?err:std::numeric_limits<float>::infinity();
                        info.worst = "point " + std::to_string(p.id) + " anim " + std::to_string(as.animId) + " t " +
                                     std::to_string(static_cast<int>(t)) + " ms";
                    }
                }
            }
        }
    }

    if (extra) points.insert(points.end(), extra->begin(), extra->end());  // mounts
    fmt::AttachTable h{};
    std::memcpy(h.magic, "WATP", 4);
    h.version = fmt::kModelFormatVersion;
    h.count = static_cast<uint16_t>(points.size());
    std::vector<uint8_t> blob(sizeof(h) + points.size() * sizeof(fmt::AttachPoint));
    std::memcpy(blob.data(), &h, sizeof(h));
    if (!points.empty()) std::memcpy(blob.data() + sizeof(h), points.data(), points.size() * sizeof(fmt::AttachPoint));
    return blob;
}

} // namespace wowee::pspconv
