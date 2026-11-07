// M9: skinned character bake for the GE (WSKN, include/rendering/psp/
// psp_model_format.hpp) and its offline check against upstream-style CPU
// skinning (verifySkinned).
//
// Bone rules are upstream CharacterRenderer's: local = T(pivot) * T * R * S *
// T(-pivot) from rendering/m2_track_sampler.hpp, world = parent * local, M2
// vertex bone indices are absolute bones, weights / 255.
#pragma once

#include "rendering/m2_track_sampler.hpp"
#include "rendering/psp/psp_skeleton.hpp"

#include "model_bake.hpp"
#include "skin_quality_policy.hpp"
#include "skin_visibility_fast.hpp"
#include "bake_progress.hpp"
#include <atomic>
#include <thread>

#include <glm/gtc/quaternion.hpp>

#include <array>
#include <limits>
#include <numeric>
#include <set>

namespace wowee::pspconv {

inline bool g_verbose = false;  // per-frame verify errors
// --palette: bones per GE draw (4..8); fewer = cheaper vertices, more draws.
inline int g_maxPalette = fmt::kSkinMaxPalette;
using fmt::kSkinMaxPalette;
using fmt::kSkinScaleOne;
using fmt::kSkinWeightOne;

// upstream CharacterRenderer::getBoneTransform: T(pivot)*T*R*S*T(-pivot).
inline glm::mat4 boneLocal(const pipeline::M2Model& m, const pipeline::M2Bone& b, int seq, float timeMs) {
    namespace tr = rendering::m2_track;
    const glm::vec3 t = tr::sampleVec3(b.translation, seq, timeMs, 0.0f, m.globalSequenceDurations, glm::vec3(0.0f));
    const glm::quat q = tr::sampleQuat(b.rotation, seq, timeMs, 0.0f, m.globalSequenceDurations);
    const glm::vec3 s = tr::sampleVec3(b.scale, seq, timeMs, 0.0f, m.globalSequenceDurations, glm::vec3(1.0f));
    const glm::mat3 r = glm::mat3_cast(q);
    const glm::vec3 c0 = r[0] * s.x, c1 = r[1] * s.y, c2 = r[2] * s.z;
    const glm::vec3 tt = (b.pivot + t) - (c0 * b.pivot.x + c1 * b.pivot.y + c2 * b.pivot.z);
    glm::mat4 out;
    out[0] = glm::vec4(c0, 0.0f);
    out[1] = glm::vec4(c1, 0.0f);
    out[2] = glm::vec4(c2, 0.0f);
    out[3] = glm::vec4(tt, 1.0f);
    return out;
}

// Every bone of the model (upstream CharacterRenderer::calculateBoneMatrices).
inline void poseBones(const pipeline::M2Model& m, int seq, float timeMs, std::vector<glm::mat4>& bones) {
    bones.assign(m.bones.size(), glm::mat4(1.0f));
    for (size_t i = 0; i < m.bones.size(); ++i) {
        const auto& b = m.bones[i];
        const glm::mat4 local = boneLocal(m, b, seq, timeMs);
        bones[i] = (b.parentBone >= 0 && static_cast<size_t>(b.parentBone) < i) ? bones[b.parentBone] * local : local;
    }
}

// CPU skinning of one vertex with its M2 weights (normalised by their sum,
// as the stand-pose bake does; upstream's weights sum to 255 anyway).
inline bool skinVertex(const pipeline::M2Vertex& v, const std::vector<glm::mat4>& bones, glm::vec3& p, glm::vec3* n) {
    glm::vec3 sp(0.0f), sn(0.0f);
    float wsum = 0.0f;
    for (int k = 0; k < 4; ++k) {
        const float w = v.boneWeights[k] / 255.0f;
        if (w <= 0.0f || v.boneIndices[k] >= bones.size()) continue;
        const glm::mat4& bm = bones[v.boneIndices[k]];
        sp += w * glm::vec3(bm * glm::vec4(v.position, 1.0f));
        sn += w * (glm::mat3(bm) * v.normal);
        wsum += w;
    }
    if (wsum <= 0.0f) return false;
    p = sp / wsum;
    if (n) *n = sn;
    return true;
}

// One source batch: triangles over the M2 vertex array + its material.
struct SkinBatchIn {
    std::vector<uint32_t> tris;  // 3 source vertex indices per triangle
    fmt::ModelBatch material{};  // texture, blend, flags (ranges are rewritten)
    uint16_t geoset = 0;
};

struct SkinBakeInfo {
    size_t bones = 0, sequences = 0, subBatches = 0, vertices = 0, triangles = 0, keys = 0;
    size_t vertexBytes = 0, animBytes = 0, trimmedInfluences = 0, weightless = 0;
    std::array<size_t, fmt::kSkinMaxPalette> paletteHistogram{};
    std::vector<uint32_t> emittedSource;  // source vertex per emitted vertex, in blob order (verify)
    // PSP: host-only oracle anchors retained when a timed grid is coarsened.
    std::vector<std::set<float>> verificationTimes;
    float standHeight = 0.0f;
};

namespace skin_detail {

inline bool trackVaries(const pipeline::M2AnimationTrack& track,int sequence) {
    if(track.globalSequence>=0||sequence<0||static_cast<size_t>(sequence)>=track.sequences.size())return false;
    const auto& keys=track.sequences[sequence];
    const auto vectors=std::min(keys.timestamps.size(),keys.vec3Values.size());
    for(size_t i=1;i<vectors;++i)if(keys.vec3Values[i]!=keys.vec3Values[0])return true;
    const auto rotations=std::min(keys.timestamps.size(),keys.quatValues.size());
    for(size_t i=1;i<rotations;++i)
        if(keys.quatValues[i]!=keys.quatValues[0]&&keys.quatValues[i]!=-keys.quatValues[0])return true;
    return false;
}

// PSP: refine only sequences whose uniform grid visibly loses authored motion.
// Use actual vertices and attachment offsets at the offline verifier's times,
// before weight/position quantization. This is a bounded quality probe, not a
// proof over continuous time; --verify still reports residual packing errors.
// Stop at 4x the requested sample count: animation memory caps still apply in
// the caller, and unresolved precision remains visible in --verify.
inline uint32_t animationIntervals(const pipeline::M2Model& model, int seq, uint32_t base,
                                  const std::vector<uint16_t>& skinBones,
                                  const std::vector<pipeline::M2Vertex>& bind,
                                  const std::vector<uint8_t>& used) {
    const float duration = static_cast<float>(model.sequences[seq].duration);
    if (!(duration > 0)) return base;
    std::set<float> times;
    for (float f : {0.13f, 0.37f, 0.5f, 0.71f, 0.93f, 0.999f}) times.insert(duration * f);
    for (int k = 1; k < 6; ++k) times.insert(duration * k / 6);
    namespace tr = rendering::m2_track;
    auto local = [&](const pipeline::M2Bone& bone, float t0, float t1, float f) {
        const auto& globals = model.globalSequenceDurations;
        const auto tv = glm::mix(tr::sampleVec3(bone.translation,seq,t0,0,globals,glm::vec3(0)),
                                 tr::sampleVec3(bone.translation,seq,t1,0,globals,glm::vec3(0)), f);
        auto q0 = tr::sampleQuat(bone.rotation,seq,t0,0,globals);
        auto q1 = tr::sampleQuat(bone.rotation,seq,t1,0,globals);
        if (glm::dot(q0,q1) < 0) q1 = -q1;
        const auto q = glm::normalize(q0 * (1-f) + q1 * f);
        const auto sv = glm::mix(tr::sampleVec3(bone.scale,seq,t0,0,globals,glm::vec3(1)),
                                 tr::sampleVec3(bone.scale,seq,t1,0,globals,glm::vec3(1)), f);
        glm::mat4 m(glm::mat3_cast(q));
        m[0] *= sv.x; m[1] *= sv.y; m[2] *= sv.z;
        m[3] = glm::vec4(bone.pivot + tv - glm::vec3(m * glm::vec4(bone.pivot,0)), 1);
        return m;
    };
    std::vector<glm::mat4> exact, approx(model.bones.size(),glm::mat4(1));
    const uint32_t limit = std::min<uint32_t>(65534, base * 4u);
    uint32_t intervals = base;
    uint32_t best = base;
    float bestError = 1e30f;
    bool triedAligned = false;
    for (;;) {
        float error = 0;
        for (float t : times) {
            const float x = t * intervals / duration;
            const uint32_t k = std::min<uint32_t>(static_cast<uint32_t>(x), intervals - 1);
            const float t0 = duration * k / intervals, t1 = duration * (k+1) / intervals;
            poseBones(model,seq,t,exact);
            for (uint16_t b : skinBones) {
                const auto& bone = model.bones[b];
                const auto l = local(bone,t0,t1,x-k);
                approx[b] = bone.parentBone >= 0 ? approx[bone.parentBone] * l : l;
            }
            for (size_t i = 0; i < bind.size(); ++i) {
                if (!used[i]) continue;
                glm::vec3 a, b;
                if (skinVertex(bind[i], exact, a, nullptr) && skinVertex(bind[i], approx, b, nullptr))
                    error = std::max(error, glm::length(a-b));
            }
            for (const auto& attachment : model.attachments) {
                if (attachment.bone >= model.bones.size() ||
                    std::find(skinBones.begin(),skinBones.end(),attachment.bone) == skinBones.end()) continue;
                const auto delta = exact[attachment.bone] - approx[attachment.bone];
                error = std::max(error,glm::length(glm::vec3(delta * glm::vec4(attachment.position,1))));
            }
        }
        if (error < bestError) { bestError = error; best = intervals; }
        if (error <= 0.01f || triedAligned) return best;
        if (intervals >= limit) {
            // PSP: a fixed 120-Hz grid can still straddle a sharp authored
            // corner (e.g. a 500-ms peak in a 1334-ms sequence). Try the
            // exact integer-ms key grid once, with a bounded PC sample budget.
            uint32_t divisor=model.sequences[seq].duration;
            for(uint16_t b:skinBones)
                for(const auto* track:{&model.bones[b].translation,&model.bones[b].rotation,&model.bones[b].scale}) {
                    if(track->globalSequence>=0||static_cast<size_t>(seq)>=track->sequences.size())continue;
                    for(uint32_t t:track->sequences[seq].timestamps)if(t<=duration)divisor=std::gcd(divisor,t);
                }
            const uint32_t aligned=divisor?model.sequences[seq].duration/divisor:0;
            if(aligned>=base&&aligned!=intervals&&aligned<=65534 &&
               uint64_t{aligned+1}*skinBones.size()<=131072) {
                intervals=aligned;triedAligned=true;continue;
            }
            return best;
        }
        intervals = std::min(limit, intervals * 2u);
    }
}

struct Influences {
    int n = 0;
    uint16_t bone[4] = {};
    float w[4] = {};
};

inline Influences influencesOf(const pipeline::M2Vertex& v, size_t boneCount) {
    Influences in;
    for (int k = 0; k < 4; ++k) {
        if (v.boneWeights[k] == 0 || v.boneIndices[k] >= boneCount) continue;
        int j = 0;
        while (j < in.n && in.bone[j] != v.boneIndices[k]) ++j;
        if (j == in.n) {
            in.bone[in.n] = v.boneIndices[k];
            in.w[in.n++] = 0.0f;
        }
        in.w[j] += v.boneWeights[k] / 255.0f;
    }
    // Heaviest first.
    for (int a = 0; a < in.n; ++a)
        for (int b = a + 1; b < in.n; ++b)
            if (in.w[b] > in.w[a]) {
                std::swap(in.w[a], in.w[b]);
                std::swap(in.bone[a], in.bone[b]);
            }
    return in;
}

// Normalised weights -> u8 with 128 = 1.0, summing to exactly 128.
template<class T>
inline void quantizeWeightsUnit(const Influences& in, T out[4], int one) {
    float sum = 0.0f;
    for (int k = 0; k < in.n; ++k) sum += in.w[k];
    int q[4] = {}, total = 0;
    float frac[4] = {};
    for (int k = 0; k < in.n; ++k) {
        const float x = in.w[k] / sum * one;
        q[k] = static_cast<int>(std::floor(x));
        frac[k] = x - q[k];
        total += q[k];
    }
    while (total < one) {
        int best = 0;
        for (int k = 1; k < in.n; ++k)
            if (frac[k] > frac[best]) best = k;
        ++q[best];
        frac[best] = -1.0f;
        ++total;
    }
    for (int k = 0; k < 4; ++k) out[k] = static_cast<T>(k < in.n ? q[k] : 0);
}
inline void quantizeWeights(const Influences& in, uint8_t out[4]) { quantizeWeightsUnit(in,out,128); }

} // namespace skin_detail

// Builds the WSKN blob. `bind`: the M2 vertices in the bind pose; `model`: the
// M2 with the sequences in `sequenceIndices` loaded (.anim files merged);
// `standSeq`: Stand (lighting and standHeight), -1 = bind pose.
inline std::vector<uint8_t> bakeSkinnedRaw(const pipeline::M2Model& model, const std::vector<pipeline::M2Vertex>& bind,
                                        const std::vector<SkinBatchIn>& batches,
                                        const std::vector<int>& sequenceIndices, int standSeq, float rate,
                                        SkinBakeInfo& info, bool animOnly = false,
                                        const std::vector<uint16_t>* keepBones = nullptr,
                                        const std::vector<uint32_t>* frameIntervals = nullptr,
                                        bool preciseWeights = false, bool virtualPalette = false,
                                        bool forceFloatPosition = false,
                                        const std::vector<std::vector<uint32_t>>* frameTimes = nullptr,
                                        bool compactTranslations = false,
                                        const std::set<std::pair<int,uint16_t>>* floatTranslationTracks = nullptr,
                                        bool slerpRotation = false, bool floatRotation = false) {
    using namespace skin_detail;
    info = SkinBakeInfo{};
    const size_t nBonesM2 = model.bones.size();
    std::vector<uint8_t> blob;
    if (nBonesM2 == 0 || batches.empty()) return blob;
    if (frameIntervals && frameIntervals->size() != sequenceIndices.size()) return blob;
    if (frameTimes && frameTimes->size() != sequenceIndices.size()) return blob;
    if((compactTranslations||slerpRotation||floatRotation)&&!frameTimes)return blob;

    // Influences of every used vertex; weightless vertices follow bone 0
    // (upstream's shader would collapse them to the origin).
    std::vector<Influences> infl(bind.size());
    std::vector<uint8_t> used(bind.size(), 0);
    for (const auto& b : batches)
        for (uint32_t v : b.tris) used[v] = 1;
    for (size_t v = 0; v < bind.size(); ++v) {
        if (!used[v]) continue;
        infl[v] = influencesOf(bind[v], nBonesM2);
        if (infl[v].n == 0) {
            infl[v].n = 1;
            infl[v].bone[0] = 0;
            infl[v].w[0] = 1.0f;
            ++info.weightless;
        }
    }
    auto triBones = [&](const uint32_t* t, std::set<uint16_t>& s) {
        s.clear();
        for (int c = 0; c < 3; ++c)
            for (int k = 0; k < infl[t[c]].n; ++k) s.insert(infl[t[c]].bone[k]);
    };
    // A triangle over more than 8 bones: drop its lightest influence (the
    // vertex keeps the rest, renormalised) until it fits one palette.
    std::set<uint16_t> tb;
    std::vector<Influences> virtualInfluences;
    std::map<std::array<uint16_t,8>,uint16_t> virtualIds;
    if (virtualPalette) {
        bool changed=true;
        while(changed) {
            changed=false;
            for(const auto& batch:batches)
                for(size_t t=0;t+2<batch.tris.size();t+=3) {
                    triBones(batch.tris.data()+t,tb);
                    while(tb.size()>static_cast<size_t>(g_maxPalette)) {
                        int best=-1;size_t count=static_cast<size_t>(-1);
                        for(int c=0;c<3;++c) {
                            const auto vi=batch.tris[t+c];const auto save=infl[vi];
                            if(save.n==1&&save.bone[0]>=nBonesM2)continue;
                            infl[vi].n=1;infl[vi].bone[0]=static_cast<uint16_t>(nBonesM2+virtualInfluences.size());
                            std::set<uint16_t> trial;triBones(batch.tris.data()+t,trial);infl[vi]=save;
                            if(trial.size()<count){best=static_cast<int>(vi);count=trial.size();}
                        }
                        if(best<0||nBonesM2+virtualInfluences.size()>65535)return {};
                        const auto original=infl[best];std::array<uint16_t,8> key{};
                        for(int k=0;k<original.n;++k){key[k]=original.bone[k];key[k+4]=static_cast<uint16_t>(std::lround(original.w[k]*255));}
                        auto [it,inserted]=virtualIds.emplace(key,static_cast<uint16_t>(nBonesM2+virtualInfluences.size()));
                        if(inserted)virtualInfluences.push_back(original);
                        infl[best].n=1;infl[best].bone[0]=it->second;infl[best].w[0]=1;
                        changed=true;triBones(batch.tris.data()+t,tb);
                    }
                }
        }
    } else for (const auto& b : batches) {
        for (size_t t = 0; t + 2 < b.tris.size(); t += 3) {
            for (triBones(&b.tris[t], tb); tb.size() > static_cast<size_t>(g_maxPalette); triBones(&b.tris[t], tb)) {
                int bestV = -1, bestK = -1;
                float bestW = 2.0f;
                for (int c = 0; c < 3; ++c) {
                    const Influences& in = infl[b.tris[t + c]];
                    if (in.n > 1 && in.w[in.n - 1] < bestW) {
                        bestW = in.w[in.n - 1];
                        bestV = static_cast<int>(b.tris[t + c]);
                        bestK = in.n - 1;
                    }
                }
                if (bestV < 0) break;
                infl[bestV].n = bestK;
                ++info.trimmedInfluences;
            }
        }
    }

    // Partition each batch into sub-batches of <= 8 bones (greedy, in skin
    // triangle order: neighbouring triangles share bones).
    struct Sub {
        size_t batch;
        std::vector<uint32_t> tris;
        std::array<int, kSkinMaxPalette> slot;  // slot -> M2 bone
        int n = 0;
    };
    std::vector<Sub> subs;
    std::vector<std::pair<uint32_t, uint32_t>> batchRange(batches.size());
    std::array<int, kSkinMaxPalette> prevSlot;
    prevSlot.fill(-1);
    for (size_t bi = 0; bi < batches.size(); ++bi) {
        std::vector<uint32_t> remaining = batches[bi].tris;
        batchRange[bi].first = static_cast<uint32_t>(subs.size());
        while (remaining.size() >= 3) {
            std::set<uint16_t> pal, u;
            std::vector<uint32_t> take, rest;
            for (size_t t = 0; t + 2 < remaining.size(); t += 3) {
                triBones(&remaining[t], tb);
                u = pal;
                u.insert(tb.begin(), tb.end());
                auto& dst = u.size() <= static_cast<size_t>(g_maxPalette) ? take : rest;
                if (&dst == &take) pal.swap(u);
                dst.insert(dst.end(), remaining.begin() + t, remaining.begin() + t + 3);
            }
            if (take.empty()) break;  // cannot happen after the trim above
            Sub s;
            s.batch = bi;
            s.tris = std::move(take);
            s.n = static_cast<int>(pal.size());
            // Slots: a bone the previous palette had in a slot < n stays there
            // (the PSP skips re-uploading an unchanged slot); the rest fill up.
            // (A bone can sit in two stale slots of prevSlot: keep the first.)
            s.slot.fill(-1);
            std::set<int> kept;
            for (int j = 0; j < s.n; ++j)
                if (prevSlot[j] >= 0 && pal.count(static_cast<uint16_t>(prevSlot[j])) && kept.insert(prevSlot[j]).second)
                    s.slot[j] = prevSlot[j];
            for (uint16_t bone : pal) {
                bool placed = false;
                for (int j = 0; j < s.n && !placed; ++j) placed = s.slot[j] == bone;
                for (int j = 0; j < s.n && !placed; ++j)
                    if (s.slot[j] < 0) {
                        s.slot[j] = bone;
                        placed = true;
                    }
            }
            for (int j = 0; j < s.n; ++j) {
                if (s.slot[j] < 0) {
                    std::fprintf(stderr, "  palette slot %d unfilled\n", j);
                    return {};
                }
                prevSlot[j] = s.slot[j];
            }
            ++info.paletteHistogram[s.n - 1];
            subs.push_back(std::move(s));
            remaining.swap(rest);
        }
        batchRange[bi].second = static_cast<uint32_t>(subs.size()) - batchRange[bi].first;
    }

    // Bones: those the palettes use plus ancestors, M2 order (parents first).
    std::vector<uint8_t> need(nBonesM2, 0);
    auto needBone=[&](int b){for(;b>=0&&!need[b];b=model.bones[b].parentBone)need[b]=1;};
    for (const auto& s : subs)
        for (int j = 0; j < s.n; ++j) {
            if(static_cast<size_t>(s.slot[j])<nBonesM2)needBone(s.slot[j]);
            else {const auto& in=virtualInfluences[s.slot[j]-nBonesM2];for(int k=0;k<in.n;++k)needBone(in.bone[k]);}
        }
    // Bones the caller needs posed although no vertex uses them
    // (attachment points), with their ancestors.
    if (keepBones)
        for (uint16_t kb : *keepBones)
            for (int b = kb; b >= 0 && static_cast<size_t>(b) < nBonesM2 && !need[b]; b = model.bones[b].parentBone)
                need[b] = 1;
    std::vector<int> toSkin(nBonesM2, -1);
    std::vector<uint16_t> skinBones;
    for (size_t b = 0; b < nBonesM2; ++b) {
        if (!need[b]) continue;
        const int parent = model.bones[b].parentBone;
        if (parent >= 0 && (static_cast<size_t>(parent) >= b || toSkin[parent] < 0)) {
            std::fprintf(stderr, "  bone %zu: parent %d not before it\n", b, parent);
            return {};
        }
        toSkin[b] = static_cast<int>(skinBones.size());
        skinBones.push_back(static_cast<uint16_t>(b));
    }
    if (skinBones.size() > 255) {
        std::fprintf(stderr, "  %zu bones > 255\n", skinBones.size());
        return {};
    }

    // Stand pose, frame 0: lighting normals and the height.
    std::vector<glm::mat4> stand;
    if (standSeq >= 0) poseBones(model, standSeq, 0.0f, stand);
    const glm::vec3 sun = glm::normalize(glm::vec3(0.35f, 0.25f, 0.9f));
    std::vector<uint8_t> unlit(bind.size(), 0);
    for (const auto& b : batches)
        if (b.material.flags & fmt::kBatchUnlit)
            for (uint32_t v : b.tris) unlit[v] = 1;
    glm::vec3 pmin(1e9f), pmax(-1e9f);
    glm::vec2 tmin(1e9f), tmax(-1e9f);
    float standTop = -1e9f;
    std::vector<uint16_t> light(bind.size(), 0xFFFF);
    for (size_t v = 0; v < bind.size(); ++v) {
        if (!used[v]) continue;
        pmin = glm::min(pmin, bind[v].position);
        pmax = glm::max(pmax, bind[v].position);
        tmin = glm::min(tmin, bind[v].texCoords[0]);
        tmax = glm::max(tmax, bind[v].texCoords[0]);
        glm::vec3 p = bind[v].position, n = bind[v].normal;
        if (!stand.empty()) skinVertex(bind[v], stand, p, &n);
        standTop = std::max(standTop, p.z);
        glm::vec3 l(1.0f);
        if (!unlit[v]) {
            const float nl = glm::length(n);
            const glm::vec3 nn = nl > 1e-4f ? n / nl : glm::vec3(0, 0, 1);
            l = glm::vec3(std::min(1.0f, 0.5f + 0.5f * std::max(0.0f, glm::dot(nn, sun))));
        }
        light[v] = to565(Rgb{l.r, l.g, l.b, 1.0f});
    }
    const glm::vec3 center = (pmin + pmax) * 0.5f;
    const glm::vec3 half = glm::max((pmax - pmin) * 0.5f, glm::vec3(0.01f));
    const glm::vec2 trange = glm::max(tmax - tmin, glm::vec2(1e-4f));
    float radius = 0.0f;
    for (size_t v = 0; v < bind.size(); ++v)
        if (used[v]) radius = std::max(radius, glm::length(bind[v].position - center));
    info.standHeight = standTop;

    // PSP: choose the accurate vertex/key representation before writing vertices.
    // Inspect authored values too, so a brief large scale between bake frames
    // cannot evade the format selection.
    bool floatScale = false;
    for (uint16_t b : skinBones)
        for (int seq : sequenceIndices) {
            const auto& track = model.bones[b].scale;
            const int index = track.globalSequence >= 0 ? 0 : seq;
            if (index < 0 || static_cast<size_t>(index) >= track.sequences.size()) continue;
            for (const auto& s : track.sequences[index].vec3Values)
                for (int a = 0; a < 3; ++a)
                    floatScale |= std::isfinite(s[a]) && std::fabs(s[a]) > 32767.0f / kSkinScaleOne;
        }
    const bool floatPosition=floatScale||forceFloatPosition;

    // Vertices: one strip per sub-batch (meshoptimizer, degenerate joins).
    std::vector<uint8_t> vbytes;
    std::vector<fmt::SkinSubBatch> outSubs;
    std::vector<std::array<fmt::SkinPaletteBlend,kSkinMaxPalette>> outPalettes;
    for (const auto& s : subs) {
        std::unordered_map<uint32_t, uint32_t> local;
        std::vector<uint32_t> src;
        std::vector<unsigned int> tri;
        for (uint32_t v : s.tris) {
            auto it = local.find(v);
            if (it == local.end()) {
                it = local.emplace(v, static_cast<uint32_t>(src.size())).first;
                src.push_back(v);
            }
            tri.push_back(it->second);
        }
        meshopt_optimizeVertexCacheStrip(tri.data(), tri.data(), tri.size(), src.size());
        std::vector<unsigned int> strip(meshopt_stripifyBound(tri.size()));
        strip.resize(meshopt_stripify(strip.data(), tri.data(), tri.size(), src.size(), 0));
        if (strip.size() > 65535) {
            std::fprintf(stderr, "  sub-batch strip too long\n");
            return {};
        }
        alignTo(vbytes, 16);
        fmt::SkinSubBatch sb{};
        sb.vertexOffset = static_cast<uint32_t>(vbytes.size());  // relative for now
        sb.vertexCount = static_cast<uint16_t>(strip.size());
        sb.weights = static_cast<uint8_t>(s.n);
        if(preciseWeights)sb.reserved|=fmt::kSkinWeights16;
        for(int j=0;j<s.n;++j)if(static_cast<size_t>(s.slot[j])>=nBonesM2)sb.reserved|=fmt::kSkinVirtualPalette;
        std::array<fmt::SkinPaletteBlend,kSkinMaxPalette> palette{};
        for (int j = 0; j < s.n; ++j) {
            if(static_cast<size_t>(s.slot[j])<nBonesM2) {
                sb.palette[j]=static_cast<uint8_t>(toSkin[s.slot[j]]);
                palette[j].bones[0]=sb.palette[j];palette[j].weights[0]=255;
            } else {
                const auto& in=virtualInfluences[s.slot[j]-nBonesM2];
                for(int k=0;k<in.n;++k){palette[j].bones[k]=static_cast<uint8_t>(toSkin[in.bone[k]]);palette[j].weights[k]=static_cast<uint8_t>(std::lround(in.w[k]*255));}
                sb.palette[j]=palette[j].bones[0];
            }
        }
        outPalettes.push_back(palette);
        const int stride = fmt::skinVertexStride(s.n, floatPosition,sb.reserved);
        for (unsigned int li : strip) {
            const uint32_t v = src[li];
            const Influences& in = infl[v];
            uint16_t q[4];
            quantizeWeightsUnit(in, q,preciseWeights?32768:128);
            std::vector<uint8_t> vert(static_cast<size_t>(stride), 0);
            for (int k = 0; k < in.n; ++k)
                for (int j = 0; j < s.n; ++j)
                    if (s.slot[j] == in.bone[k]) {
                        if(preciseWeights)std::memcpy(vert.data()+j*2,q+k,2);
                        else vert[j]=static_cast<uint8_t>(vert[j]+q[k]);
                    }
            size_t o = static_cast<size_t>(fmt::skinWeightBytes(s.n,sb.reserved));
            const glm::vec2 uv = bind[v].texCoords[0];
            const glm::vec3 p = (bind[v].position - center) / half;
            auto w16 = [&](uint16_t x) {
                std::memcpy(&vert[o], &x, 2);
                o += 2;
            };
            w16(static_cast<uint16_t>(std::lround(std::clamp((uv.x - tmin.x) / trange.x, 0.0f, 1.0f) * 65535.0f)));
            w16(static_cast<uint16_t>(std::lround(std::clamp((uv.y - tmin.y) / trange.y, 0.0f, 1.0f) * 65535.0f)));
            w16(light[v]);
            if (floatPosition) {
                const float xyz[] = {p.x, p.y, p.z};
                std::memcpy(vert.data() + fmt::skinPositionOffset(s.n, true,sb.reserved), xyz, sizeof(xyz));
            } else {
                for (int a = 0; a < 3; ++a)
                    w16(static_cast<uint16_t>(static_cast<int16_t>(std::lround(std::clamp(p[a], -1.0f, 1.0f) * 32767.0f))));
            }
            vbytes.insert(vbytes.end(), vert.begin(), vert.end());
            info.emittedSource.push_back(v);
        }
        info.triangles += s.tris.size() / 3;
        info.vertices += strip.size();
        outSubs.push_back(sb);
    }

    // Sequences: every bone sampled with the upstream sampler at a fixed rate.
    struct Sample {
        glm::quat r;
        glm::vec3 t, s;
    };
    const size_t nb = skinBones.size();
    std::vector<fmt::AnimSequence> seqs;
    std::vector<std::vector<Sample>> samples;  // [seq][bone * frames + k]
    float maxT = 1e-6f;
    const bool floatScaleKeys=floatPosition||preciseWeights||!virtualInfluences.empty()||frameTimes;
    for (int si : sequenceIndices) {
        const auto& ms = model.sequences[si];
        const uint32_t base = std::min<uint32_t>(65534u, std::max<uint32_t>(1u,
            static_cast<uint32_t>(std::lround(ms.duration * rate / 1000.0f))));
        if(frameTimes) {
            const auto& times=(*frameTimes)[seqs.size()];
            if(times.size()<2||times.size()>65535||times.front()!=0||times.back()!=ms.duration)return {};
            for(size_t i=1;i<times.size();++i)
                if(times[i]<=times[i-1] && !(ms.duration==0&&times.size()==2&&times[i]==0))return {};
        }
        const uint32_t k = frameTimes?static_cast<uint32_t>((*frameTimes)[seqs.size()].size()-1):frameIntervals
            ? std::clamp((*frameIntervals)[seqs.size()], 1u, 65534u)
            : animationIntervals(model, si, base, skinBones, bind, used);
        fmt::AnimSequence as{};
        as.animId = static_cast<uint16_t>(ms.id);
        as.variation = static_cast<uint16_t>(ms.variationIndex);
        as.durationMs = ms.duration;
        as.frameCount = static_cast<uint16_t>(std::min<uint32_t>(k + 1u, 65535u));
        as.flags = static_cast<uint16_t>(ms.flags);
        as.movingSpeed = ms.movingSpeed;
        as.frequency = ms.frequency;
        as.replayMin = static_cast<uint16_t>(std::min<uint32_t>(ms.replayMin, 65535u));
        as.replayMax = static_cast<uint16_t>(std::min<uint32_t>(ms.replayMax, 65535u));
        as.blendTimeMs = static_cast<uint16_t>(std::min<uint32_t>(ms.blendTime, 65535u));
        const uint32_t frames = as.frameCount;
        std::vector<Sample> sm(nb * frames);
        for (size_t b = 0; b < nb; ++b) {
            const auto& bone = model.bones[skinBones[b]];
            namespace tr = rendering::m2_track;
            for (uint32_t f = 0; f < frames; ++f) {
                const float t = frameTimes?static_cast<float>((*frameTimes)[seqs.size()][f]):
                    static_cast<float>(ms.duration) * static_cast<float>(f) / static_cast<float>(frames - 1);
                Sample& x = sm[b * frames + f];
                x.t = tr::sampleVec3(bone.translation, si, t, 0.0f, model.globalSequenceDurations, glm::vec3(0.0f));
                x.r = tr::sampleQuat(bone.rotation, si, t, 0.0f, model.globalSequenceDurations);
                x.s = tr::sampleVec3(bone.scale, si, t, 0.0f, model.globalSequenceDurations, glm::vec3(1.0f));
                for (int a = 0; a < 3; ++a)
                    floatScale |= std::fabs(x.s[a]) > 32767.0f / kSkinScaleOne;
                if (f > 0 && glm::dot(x.r, sm[b * frames + f - 1].r) < 0.0f) x.r = -x.r;
                for (int a = 0; a < 3; ++a) maxT = std::max(maxT, std::fabs(x.t[a]));
            }
        }
        seqs.push_back(as);
        samples.push_back(std::move(sm));
    }
    // PSP: v4 float translations do not use this base. Keep compact tracks
    // precise even when unrelated tracks contain enormous source translations;
    // tracks outside its range are written as floats below, never clipped.
    const float tScale = frameTimes&&compactTranslations?std::min(maxT/32767.0f,1.0f/64.0f):maxT/32767.0f;
    using Key = std::array<int16_t, 4>;
    std::vector<Key> keys;
    std::map<Key, uint32_t> constKeys;
    std::map<std::array<int16_t, 8>, uint32_t> constScaleKeys;
    std::map<std::vector<Key>,uint32_t> animatedKeys;
    auto q16 = [](float x) { return static_cast<int16_t>(std::lround(std::clamp(x, -32767.0f, 32767.0f))); };
    std::vector<fmt::BoneTrack> tracks;
    for (size_t si = 0; si < seqs.size(); ++si) {
        const uint32_t frames = seqs[si].frameCount;
        for (size_t b = 0; b < nb; ++b) {
            fmt::BoneTrack tr{fmt::kTrackNone, fmt::kTrackNone, fmt::kTrackNone};
            bool compactT=compactTranslations&&(!floatTranslationTracks||
                !floatTranslationTracks->count({sequenceIndices[si],skinBones[b]}));
            if(compactT)for(uint32_t f=0;f<frames;++f)for(unsigned a=0;a<3;++a)
                if(std::fabs(samples[si][b*frames+f].t[a])>32767.0f*tScale)compactT=false;
            for (int comp = 0; comp < 3; ++comp) {
                // PSP: large authored scales (e.g. growing effect meshes) cannot
                // fit v1's signed 3.12 fixed point. Preserve their float values;
                // two key slots per frame keep all other offsets/layouts intact.
                if ((comp == 0 && floatRotation) || (comp == 2 && floatScaleKeys) || (comp == 1 && frameTimes&&!compactT)) {
                    std::vector<std::array<int16_t, 8>> sk(frames);
                    bool constant = true;
                    for (uint32_t f = 0; f < frames; ++f) {
                        const auto& sample=samples[si][b*frames+f];
                        const glm::vec3 scale=comp==0?glm::vec3(sample.r.x,sample.r.y,sample.r.z):comp==2?sample.s:sample.t;
                        const float xyz[] = {scale.x,scale.y,scale.z,comp==0?sample.r.w:0.0f};
                        std::memcpy(sk[f].data(), xyz, sizeof(xyz));
                        if (f > 0 && sk[f] != sk[0]) constant = false;
                    }
                    const float one[] = {comp==2?1.0f:0.0f,comp==2?1.0f:0.0f,comp==2?1.0f:0.0f,comp==0?1.0f:0.0f};
                    std::array<int16_t, 8> def{};
                    std::memcpy(def.data(), one, sizeof(one));
                    if (constant && sk[0] == def) continue;
                    auto appendScale = [&](const std::array<int16_t, 8>& scale) {
                        Key lo, hi;
                        std::copy_n(scale.begin(), 4, lo.begin());
                        std::copy_n(scale.begin() + 4, 4, hi.begin());
                        keys.push_back(lo);
                        keys.push_back(hi);
                    };
                    if (constant) {
                        auto [it, inserted] = constScaleKeys.emplace(sk[0], static_cast<uint32_t>(keys.size()));
                        if (inserted) appendScale(sk[0]);
                        (comp==0?tr.rotation:comp==2?tr.scale:tr.translation) = it->second;
                    } else {
                        std::vector<Key> flat;flat.reserve(sk.size()*2);
                        for(const auto& scale:sk) {
                            Key lo,hi;std::copy_n(scale.begin(),4,lo.begin());std::copy_n(scale.begin()+4,4,hi.begin());
                            flat.push_back(lo);flat.push_back(hi);
                        }
                        auto [it,inserted]=animatedKeys.emplace(flat,static_cast<uint32_t>(keys.size()));
                        if(inserted)keys.insert(keys.end(),flat.begin(),flat.end());
                        (comp==0?tr.rotation:comp==2?tr.scale:tr.translation)=it->second|fmt::kTrackAnimated;
                    }
                    continue;
                }
                uint16_t translationExponents=0;glm::vec3 translationStep(tScale);
                if(comp==1&&frameTimes&&compactT) {
                    glm::vec3 maximum(0);
                    for(uint32_t f=0;f<frames;++f)maximum=glm::max(maximum,glm::abs(samples[si][b*frames+f].t));
                    for(int a=0;a<3;++a) {
                        unsigned exponent=0;
                        while(exponent<31&&maximum[a]<=std::ldexp(tScale*32767.0f,-static_cast<int>(exponent+1)))++exponent;
                        translationExponents|=static_cast<uint16_t>(exponent<<(a*5));
                        translationStep[a]=std::ldexp(tScale,-static_cast<int>(exponent));
                    }
                }
                std::vector<Key> ks(frames);
                for (uint32_t f = 0; f < frames; ++f) {
                    const Sample& x = samples[si][b * frames + f];
                    if (comp == 0)
                        ks[f] = {q16(x.r.x * 32767.0f), q16(x.r.y * 32767.0f), q16(x.r.z * 32767.0f),
                                 q16(x.r.w * 32767.0f)};
                    else if (comp == 1)
                        ks[f] = {q16(x.t.x / translationStep.x), q16(x.t.y / translationStep.y), q16(x.t.z / translationStep.z),
                            static_cast<int16_t>(translationExponents)};
                    else
                        ks[f] = {q16(x.s.x * kSkinScaleOne), q16(x.s.y * kSkinScaleOne), q16(x.s.z * kSkinScaleOne), 0};
                }
                bool constant = true;
                for (uint32_t f = 1; f < frames && constant; ++f) constant = ks[f] == ks[0];
                const Key def = comp == 0 ? Key{0, 0, 0, 32767}
                                          : comp == 1 ? Key{0, 0, 0, 0}
                                                      : Key{4096, 4096, 4096, 0};
                const Key defNeg{0, 0, 0, -32767};
                uint32_t ref = fmt::kTrackNone;
                const bool compactZero=comp==1&&frameTimes&&compactT&&ks[0][0]==0&&ks[0][1]==0&&ks[0][2]==0;
                if (constant && (ks[0] == def || compactZero || (comp == 0 && ks[0] == defNeg))) {
                    ref = fmt::kTrackNone;
                } else if (constant) {
                    auto it = constKeys.find(ks[0]);
                    if (it == constKeys.end()) {
                        it = constKeys.emplace(ks[0], static_cast<uint32_t>(keys.size())).first;
                        keys.push_back(ks[0]);
                    }
                    ref = it->second;
                } else {
                    // PSP: aliases/related sequences often contain identical
                    // motion. Share exact bytes rather than spending the PSP's
                    // animation budget on another copy of the same track.
                    auto [it,inserted]=animatedKeys.emplace(ks,static_cast<uint32_t>(keys.size()));
                    if(inserted)keys.insert(keys.end(),ks.begin(),ks.end());
                    ref=it->second|fmt::kTrackAnimated;
                }
                if(comp==1&&frameTimes&&compactT&&ref!=fmt::kTrackNone)ref|=fmt::kTrackCompactTranslation;
                (comp == 0 ? tr.rotation : comp == 1 ? tr.translation : tr.scale) = ref;
            }
            tracks.push_back(tr);
        }
    }

    // Assemble.
    fmt::SkinHeader h{};
    std::memcpy(h.magic, animOnly ? "WSKA" : "WSKN", 4);
    h.version = frameTimes?fmt::kSkinTimedVersion:preciseWeights||!virtualInfluences.empty()||forceFloatPosition ? fmt::kSkinPrecisionVersion :
        floatScale ? fmt::kSkinFloatScaleVersion : fmt::kSkinFormatVersion;
    if (floatPosition) h.flags |= fmt::kSkinFloatPosition;
    if(slerpRotation)h.flags|=fmt::kSkinSlerpRotation;
    if(floatRotation)h.flags|=fmt::kSkinFloatRotation;
    h.boneCount = static_cast<uint16_t>(nb);
    h.sequenceCount = static_cast<uint16_t>(seqs.size());
    h.batchCount = animOnly ? 0 : static_cast<uint16_t>(batches.size());
    h.subBatchCount = animOnly ? 0 : static_cast<uint16_t>(outSubs.size());
    for (int a = 0; a < 3; ++a) {
        h.center[a] = center[a];
        h.half[a] = half[a];
    }
    h.uvMin[0] = tmin.x;
    h.uvMin[1] = tmin.y;
    h.uvScale[0] = trange.x * 32768.0f / 65535.0f;
    h.uvScale[1] = trange.y * 32768.0f / 65535.0f;
    h.standHeight = standTop;
    h.radius = radius;
    h.translationScale = tScale;
    h.sampleRate = rate;
    blob.resize(sizeof(h)+(frameTimes?8u:0u));
    auto section = [&](const void* p, size_t bytes) {
        alignTo(blob, 4);
        const uint32_t off = static_cast<uint32_t>(blob.size());
        blob.insert(blob.end(), static_cast<const uint8_t*>(p), static_cast<const uint8_t*>(p) + bytes);
        return off;
    };
    size_t timingBytes=0;
    auto appendTiming=[&]() {
        if(!frameTimes)return;
        std::vector<uint8_t> table={'W','T','I','M'};put<uint32_t>(table,1);put<uint32_t>(table,static_cast<uint32_t>(seqs.size()));
        table.resize(12+seqs.size()*8,0);const uint32_t at=section(table.data(),table.size());
        timingBytes=8+table.size();
        for(size_t s=0;s<seqs.size();++s) {
            const auto& times=(*frameTimes)[s];const uint32_t offset=section(times.data(),times.size()*4u);
            const uint32_t count=static_cast<uint32_t>(times.size());
            std::memcpy(blob.data()+at+12+s*8,&offset,4);std::memcpy(blob.data()+at+16+s*8,&count,4);
            timingBytes+=times.size()*4u;
        }
        std::memcpy(blob.data()+sizeof(h),&at,4);
    };
    std::vector<fmt::SkinBone> bonesOut(nb);
    for (size_t b = 0; b < nb; ++b) {
        const auto& mb = model.bones[skinBones[b]];
        bonesOut[b].parent = static_cast<int16_t>(mb.parentBone >= 0 ? toSkin[mb.parentBone] : -1);
        bonesOut[b].m2Bone = skinBones[b];
        for (int a = 0; a < 3; ++a) bonesOut[b].pivot[a] = mb.pivot[a];
    }
    h.boneOffset = section(bonesOut.data(), bonesOut.size() * sizeof(fmt::SkinBone));
    h.sequenceOffset = section(seqs.data(), seqs.size() * sizeof(fmt::AnimSequence));
    if (animOnly) {  // WSKA: skeleton + sequences only (emotes, loaded on demand)
        h.batchOffset = h.geosetOffset = h.subBatchOffset = h.sequenceOffset;
        h.trackOffset = section(tracks.data(), tracks.size() * sizeof(fmt::BoneTrack));
        h.keyOffset = section(keys.data(), keys.size() * sizeof(Key));
        h.keyCount = static_cast<uint32_t>(keys.size());
        appendTiming();
        alignTo(blob, 4);
        std::memcpy(blob.data(), &h, sizeof(h));
        info.bones = nb;
        info.sequences = seqs.size();
        info.keys = keys.size();
        info.animBytes = tracks.size() * sizeof(fmt::BoneTrack) + keys.size() * sizeof(Key)+timingBytes;
        return blob;
    }
    std::vector<fmt::ModelBatch> mbs(batches.size());
    std::vector<uint16_t> geos(batches.size());
    for (size_t b = 0; b < batches.size(); ++b) {
        mbs[b] = batches[b].material;
        mbs[b].indexStart = batchRange[b].first;
        mbs[b].indexCount = batchRange[b].second;
        geos[b] = batches[b].geoset;
    }
    h.batchOffset = section(mbs.data(), mbs.size() * sizeof(fmt::ModelBatch));
    h.geosetOffset = section(geos.data(), geos.size() * sizeof(uint16_t));
    h.subBatchOffset = section(outSubs.data(), outSubs.size() * sizeof(fmt::SkinSubBatch));
    h.trackOffset = section(tracks.data(), tracks.size() * sizeof(fmt::BoneTrack));
    h.keyOffset = section(keys.data(), keys.size() * sizeof(Key));
    h.keyCount = static_cast<uint32_t>(keys.size());
    if(!virtualInfluences.empty()) {
        h.flags|=fmt::kSkinHasVirtualPalette;
        fmt::SkinPaletteHeader ph{};std::memcpy(ph.magic,"WVPL",4);ph.version=1;ph.slots=kSkinMaxPalette;
        ph.subBatchCount=static_cast<uint32_t>(outSubs.size());
        std::vector<uint8_t> table;const auto* pp=reinterpret_cast<const uint8_t*>(&ph);table.insert(table.end(),pp,pp+sizeof(ph));
        const auto* vp=reinterpret_cast<const uint8_t*>(outPalettes.data());table.insert(table.end(),vp,vp+outPalettes.size()*sizeof(outPalettes[0]));
        h.reserved[2]=section(table.data(),table.size());
    }
    appendTiming();
    alignTo(blob, 16);
    const uint32_t vbase = static_cast<uint32_t>(blob.size());
    blob.insert(blob.end(), vbytes.begin(), vbytes.end());
    h.vertexBytes = static_cast<uint32_t>(vbytes.size());
    for (size_t i = 0; i < outSubs.size(); ++i) {
        fmt::SkinSubBatch sb = outSubs[i];
        sb.vertexOffset += vbase;
        std::memcpy(&blob[h.subBatchOffset + i * sizeof(sb)], &sb, sizeof(sb));
    }
    std::memcpy(blob.data(), &h, sizeof(h));

    info.bones = nb;
    info.sequences = seqs.size();
    info.subBatches = outSubs.size();
    info.keys = keys.size();
    info.vertexBytes = vbytes.size();
    info.animBytes = tracks.size() * sizeof(fmt::BoneTrack) + keys.size() * sizeof(Key)+timingBytes;
    return blob;
}

// Offline proof of the PSP path: decode the blob exactly as the GE sees it
// (s16 / 32768, u8 weights / 128, bone matrices from psp_skeleton.hpp with the
// dequantisation folded in) and compare every emitted vertex with upstream-
// style CPU skinning of its source vertex (m2_track sampler at the same
// time, all M2 bones). Times off the baked frame grid measure the resampling.
struct SkinVerifyResult {
    double maxErr = 0.0, sumSq = 0.0;
    size_t samples = 0, probeTimes = 0, uncertaintySamples = 0;
    float rawMaxErr=0;size_t clippedSamples=0; // PSP: explicit scoped-proof metrics
    std::string worst;
};

// Posed triangles for the offline renderer: float xyz[3] + u16 geoset + u16 pad.
inline void appendTri(std::vector<uint8_t>& dump, const glm::vec3& a, const glm::vec3& b, const glm::vec3& c,
                      uint16_t geoset) {
    for (const glm::vec3* p : {&a, &b, &c})
        for (int k = 0; k < 3; ++k) put<float>(dump, (*p)[k]);
    put<uint16_t>(dump, geoset);
    put<uint16_t>(dump, 0);
}

inline glm::vec3 geSkin(const fmt::SkinView& view, const std::vector<fmt::BoneMatrix>& pose,
                        const fmt::SkinSubBatch& sb, const uint8_t* vert) {
    glm::vec3 pn;
    const bool floatPosition = (view.h->flags & fmt::kSkinFloatPosition) != 0;
    if (floatPosition) {
        float xyz[3];
        std::memcpy(xyz, vert + fmt::skinPositionOffset(sb.weights, true,sb.reserved), sizeof(xyz));
        pn = glm::vec3(xyz[0], xyz[1], xyz[2]);
    } else {
        int16_t xyz[3];
        std::memcpy(xyz, vert + fmt::skinPositionOffset(sb.weights, false,sb.reserved), sizeof(xyz));
        pn = glm::vec3(xyz[0] / 32768.0f, xyz[1] / 32768.0f, xyz[2] / 32768.0f);
    }
    glm::vec3 out(0.0f);
    for (int j = 0; j < sb.weights; ++j) {
        uint16_t weight=vert[j];
        if(sb.reserved&fmt::kSkinWeights16)std::memcpy(&weight,vert+j*2,2);
        const float w = weight / ((sb.reserved&fmt::kSkinWeights16)?32768.0f:kSkinWeightOne);
        if (w == 0.0f) continue;
        float g[12];
        const auto* blend=view.paletteBlend(sb,j);
        if(blend) {
            fmt::BoneMatrix matrix;
            fmt::skinBlendPaletteMatrix(*blend,pose[0].m,12,matrix.m,12);
            fmt::geBoneMatrix(matrix,*view.h,g);
        } else fmt::geBoneMatrix(pose[sb.palette[j]], *view.h, g);
        for (int r = 0; r < 3; ++r) out[r] += w * (g[r] * pn.x + g[3 + r] * pn.y + g[6 + r] * pn.z + g[9 + r]);
    }
    return out;
}

// PSP: decode immutable vertex data once and fold each GE palette slot once
// per pose. Preserve geSkin's operation order, including virtual blends and
// weight normalization; this changes work, not the packed-path oracle.
class SkinOraclePlan {
    struct Vertex {glm::vec3 position;std::array<float,fmt::kSkinMaxPalette> weights{};uint32_t batch;};
    using Matrices=std::array<std::array<float,12>,fmt::kSkinMaxPalette>;
    const fmt::SkinView& view_;
    std::vector<Vertex> vertices_;
    std::vector<Matrices> matrices_;
    std::vector<uint8_t> used_;
public:
    explicit SkinOraclePlan(const fmt::SkinView& view):view_(view),matrices_(view.h->subBatchCount),used_(view.h->subBatchCount) {
        for(uint32_t b=0;b<view.h->batchCount;++b)for(uint32_t k=0;k<view.batches[b].indexCount;++k) {
            const auto index=view.batches[b].indexStart+k;const auto& sb=view.subBatches[index];used_[index]=1;
            const bool fp=(view.h->flags&fmt::kSkinFloatPosition)!=0;
            const int stride=fmt::skinVertexStride(sb.weights,fp,sb.reserved);
            for(uint32_t i=0;i<sb.vertexCount;++i) {
                const auto* data=view.vertices(sb)+i*stride;Vertex v{};v.batch=index;
                const auto offset=fmt::skinPositionOffset(sb.weights,fp,sb.reserved);
                if(fp) {float p[3];std::memcpy(p,data+offset,sizeof(p));v.position={p[0],p[1],p[2]};}
                else {int16_t p[3];std::memcpy(p,data+offset,sizeof(p));v.position={p[0]/32768.0f,p[1]/32768.0f,p[2]/32768.0f};}
                for(unsigned j=0;j<sb.weights;++j) {
                    uint16_t weight=data[j];if(sb.reserved&fmt::kSkinWeights16)std::memcpy(&weight,data+j*2,2);
                    v.weights[j]=weight/((sb.reserved&fmt::kSkinWeights16)?32768.0f:kSkinWeightOne);
                }
                vertices_.push_back(v);
            }
        }
    }
    void prepare(const std::vector<fmt::BoneMatrix>& pose) {
        for(uint32_t i=0;i<view_.h->subBatchCount;++i)if(used_[i]) {
            const auto& sb=view_.subBatches[i];
            for(unsigned j=0;j<sb.weights;++j) {
                const auto* blend=view_.paletteBlend(sb,j);
                if(blend) {fmt::BoneMatrix matrix;fmt::skinBlendPaletteMatrix(*blend,pose[0].m,12,matrix.m,12);
                    fmt::geBoneMatrix(matrix,*view_.h,matrices_[i][j].data());}
                else fmt::geBoneMatrix(pose[sb.palette[j]],*view_.h,matrices_[i][j].data());
            }
        }
    }
    glm::vec3 skin(size_t i) const {
        const auto& v=vertices_[i];const auto& sb=view_.subBatches[v.batch];glm::vec3 out(0.0f);
        for(unsigned j=0;j<sb.weights;++j) {
            const float w=v.weights[j];if(w==0.0f)continue;const auto& g=matrices_[v.batch][j];
            for(int r=0;r<3;++r)out[r]+=w*(g[r]*v.position.x+g[3+r]*v.position.y+g[6+r]*v.position.z+g[9+r]);
        }
        return out;
    }
};

inline thread_local const char* skinOracleOperation="oracle";
struct SkinOracleOperationScope {
    const char* previous;
    explicit SkinOracleOperationScope(const char* name):previous(skinOracleOperation){skinOracleOperation=name;}
    ~SkinOracleOperationScope(){skinOracleOperation=previous;}
};

// PSP: measure the actual packed path, so weight/position quantization cannot
// be mistaken for motion that would benefit from more animation keys.
inline std::set<float> skinProbeTimes(const fmt::SkinView& view,uint32_t sequence,
                                     const pipeline::M2Model& model,int m2Sequence,bool includeAuthored,
                                     const std::set<float>* pinnedTimes=nullptr,
                                     const SkinVisibilityPolicy* visibility=nullptr) {
    std::set<float> fractions={0,.13f,.37f,.5f,.71f,.93f,.999f,1};
    for(int k=1;k<6;++k)fractions.insert(k/6.0f);
    std::set<float> times;
    for(float f:fractions)times.insert(view.sequences[sequence].durationMs*f);
    if(includeAuthored)
        for(uint32_t b=0;b<view.h->boneCount;++b)
            for(const auto* track:{&model.bones[view.bones[b].m2Bone].translation,
                                  &model.bones[view.bones[b].m2Bone].rotation,&model.bones[view.bones[b].m2Bone].scale}) {
                if(track->globalSequence>=0||m2Sequence<0||static_cast<size_t>(m2Sequence)>=track->sequences.size())continue;
                for(uint32_t t:track->sequences[m2Sequence].timestamps)
                    if(t<=view.sequences[sequence].durationMs)times.insert(static_cast<float>(t));
            }
    if(includeAuthored) {
        const auto* frameTimes=view.sequenceTimes(sequence);
        const uint32_t frames=view.sequences[sequence].frameCount;
        for(uint32_t f=0;f<frames;++f) {
            const float t=frameTimes?static_cast<float>(frameTimes[f]):
                static_cast<float>(view.sequences[sequence].durationMs)*f/(frames-1);
            times.insert(t);
            if(f) {
                const float previous=frameTimes?static_cast<float>(frameTimes[f-1]):
                    static_cast<float>(view.sequences[sequence].durationMs)*(f-1)/(frames-1);
                for(float part:{.25f,.5f,.75f})times.insert(previous+(t-previous)*part);
            }
        }
    }
    // PSP: reproducible legacy30Hz quarters replace the private old-pack time
    // dump. Neighbor all source/candidate/legacy anchors before unioning older
    // pins, so repeated coarsening does not grow nextafter chains indefinitely.
    if(visibility&&includeAuthored) {
        const float duration=static_cast<float>(view.sequences[sequence].durationMs);
        const uint32_t intervals=std::min<uint32_t>(65534,std::max<uint32_t>(1,
            static_cast<uint32_t>(std::lround(duration*30.0f/1000.0f))));
        for(uint32_t f=0;f<=intervals;++f) {
            const float time=duration*f/intervals;times.insert(time);
            if(f) {
                const float previous=duration*(f-1)/intervals;
                for(float fraction:{.25f,.5f,.75f})times.insert(previous+(time-previous)*fraction);
            }
        }
        const auto anchors=times;
        for(float time:anchors) {
            const float before=std::nextafter(time,-std::numeric_limits<float>::infinity());
            const float after=std::nextafter(time,std::numeric_limits<float>::infinity());
            if(before>=0)times.insert(before);
            if(after<=duration)times.insert(after);
        }
    }
    if(pinnedTimes)times.insert(pinnedTimes->begin(),pinnedTimes->end());
    return times;
}

// PSP: opt-in only after source AND consumer qualification. Every point stays
// absolute; only whole triangles jointly outside the audited envelope can skip.
inline float skinScopedSequenceError(const fmt::SkinView& view,uint32_t sequence,
        const pipeline::M2Model& model,int m2Sequence,const std::vector<pipeline::M2Vertex>& bind,
        const std::vector<uint32_t>& emittedSource,bool includeAuthored,std::set<uint16_t>* badBones,
        size_t* vertexSamples,size_t* probeCount,double* sumSq,std::string* worst,
        const std::set<float>* pinnedTimes,std::set<float>* badTimes,const SkinVisibilityPolicy& policy,
        float* rawMaximum,size_t* clippedSamples) {
    namespace visibility=skin_visibility;
    struct DrawVertex {const fmt::SkinSubBatch* batch;const uint8_t* data;};
    std::vector<DrawVertex> draw;std::vector<visibility::IndexTriangle> triangles;
    for(uint32_t b=0;b<view.h->batchCount;++b)for(uint32_t k=0;k<view.batches[b].indexCount;++k) {
        const auto& sb=view.subBatches[view.batches[b].indexStart+k];const size_t first=draw.size();
        const int stride=fmt::skinVertexStride(sb.weights,(view.h->flags&fmt::kSkinFloatPosition)!=0,sb.reserved);
        for(uint32_t i=0;i<sb.vertexCount;++i)draw.push_back({&sb,view.vertices(sb)+i*stride});
        for(uint32_t i=2;i<sb.vertexCount;++i)triangles.push_back({first+i-2,first+i-1,first+i});
    }
    if(draw.size()!=emittedSource.size())return 1e30f;
    for(uint32_t vi:emittedSource)if(vi>=bind.size())return 1e30f;
    const auto topology=visibility::prepareTopology(draw.size(),triangles,emittedSource);
    if(!topology.valid)return 1e30f;
    const auto times=skinProbeTimes(view,sequence,model,m2Sequence,includeAuthored,pinnedTimes,&policy);
    const std::vector<float> values(times.begin(),times.end());
    struct Result {float error=0,raw=0;size_t samples=0,clipped=0;double sum=0;bool bad=false;
        std::string worst;std::set<uint16_t> bones;};
    std::vector<Result> results(values.size());std::atomic<size_t> next{0};std::atomic<bool> failed{false};
    auto worker=[&]() {
        try {
            visibility::PoseWorkspace workspace;
            std::vector<fmt::BoneMatrix> pose(view.h->boneCount);std::vector<glm::mat4> reference;
            std::vector<glm::vec3> source(bind.size());std::vector<uint8_t> ready(bind.size());
            std::vector<visibility::Point3> expected(draw.size()),actual(draw.size());
            for(size_t index;(index=next.fetch_add(1))<values.size();) {
                const float time=values[index];auto& result=results[index];
                fmt::evaluatePose(view,sequence,time,pose.data());poseBones(model,m2Sequence,time,reference);
                std::fill(ready.begin(),ready.end(),0);
                for(size_t i=0;i<draw.size();++i) {
                    const auto vi=emittedSource[i];
                    if(!ready[vi]){if(!skinVertex(bind[vi],reference,source[vi],nullptr))source[vi]=bind[vi].position;ready[vi]=1;}
                    const auto point=geSkin(view,pose,*draw[i].batch,draw[i].data);
                    expected[i]={source[vi].x,source[vi].y,source[vi].z};actual[i]={point.x,point.y,point.z};
                }
                const auto& proof=visibility::classifyMeshFast(expected,actual,topology,policy.envelope,workspace);
                if(!proof.inputValid){result.error=1e30f;result.worst="invalid visibility inputs";continue;}
                for(size_t i=0;i<draw.size();++i) {
                    const auto vi=emittedSource[i];const auto& vertex=bind[vi];
                    const glm::vec3 got(static_cast<float>(actual[i][0]),static_cast<float>(actual[i][1]),static_cast<float>(actual[i][2]));
                    const float distance=glm::length(got-source[vi]);
                    if(!std::isfinite(distance)){result.error=1e30f;result.worst="nonfinite visibility error";break;}
                    result.raw=std::max(result.raw,distance);
                    if(proof.canSkipInstance[i]){++result.clipped;continue;}
                    ++result.samples;result.sum+=static_cast<double>(distance)*distance;
                    if(distance>result.error)result.worst="anim "+std::to_string(view.sequences[sequence].animId)+
                        " t="+std::to_string(time)+" vertex "+std::to_string(vi);
                    result.error=std::max(result.error,distance);result.bad|=distance>.02f;
                    if(distance>.02f&&badBones)for(unsigned k=0;k<4;++k)
                        if(vertex.boneWeights[k]&&vertex.boneIndices[k]<model.bones.size())result.bones.insert(vertex.boneIndices[k]);
                }
                for(const auto& attachment:model.attachments) {
                    uint32_t bone=0;while(bone<view.h->boneCount&&view.bones[bone].m2Bone!=attachment.bone)++bone;
                    if(bone==view.h->boneCount||attachment.bone>=reference.size())continue;
                    const auto& matrix=pose[bone];const auto p=attachment.position;glm::vec3 got;
                    for(unsigned r=0;r<3;++r)got[r]=matrix.m[r]*p.x+matrix.m[r+3]*p.y+matrix.m[r+6]*p.z+matrix.m[r+9];
                    const float distance=glm::length(got-glm::vec3(reference[attachment.bone]*glm::vec4(p,1)));
                    if(!std::isfinite(distance)){result.error=1e30f;result.worst="nonfinite point error";break;}
                    result.raw=std::max(result.raw,distance);
                    if(distance>result.error)result.worst="point "+std::to_string(attachment.id)+" anim "+
                        std::to_string(view.sequences[sequence].animId)+" t="+std::to_string(time);
                    result.error=std::max(result.error,distance);result.bad|=distance>.02f;
                    if(distance>.02f&&badBones)result.bones.insert(attachment.bone);
                }
            }
        } catch(...) {failed.store(true);}
    };
    // Only this one qualified source adds bounded host workers. State is local
    // per call/thread; chronological reduction keeps deterministic tie ordering.
    std::vector<std::thread> workers;
    try {for(size_t i=1;i<std::min<size_t>(4,values.size());++i)workers.emplace_back(worker);}
    catch(...){for(auto& thread:workers)thread.join();throw;}
    worker();for(auto& thread:workers)thread.join();
    if(failed.load()){if(worst)*worst="visibility worker failed";return 1e30f;}
    if(vertexSamples)*vertexSamples=0;
    if(probeCount)*probeCount=values.size();
    if(sumSq)*sumSq=0;
    if(rawMaximum)*rawMaximum=0;
    if(clippedSamples)*clippedSamples=0;
    float error=0;
    for(size_t i=0;i<results.size();++i) {
        const auto& result=results[i];
        if(vertexSamples)*vertexSamples+=result.samples;
        if(sumSq)*sumSq+=result.sum;
        if(rawMaximum)*rawMaximum=std::max(*rawMaximum,result.raw);
        if(clippedSamples)*clippedSamples+=result.clipped;
        if(badBones)badBones->insert(result.bones.begin(),result.bones.end());
        if(result.bad&&badTimes)badTimes->insert(values[i]);
        if(result.error>error){error=result.error;if(worst)*worst=result.worst;}
    }
    return error;
}

inline float skinSequenceError(const fmt::SkinView& view, uint32_t sequence,
                               const pipeline::M2Model& model, int m2Sequence,
                               const std::vector<pipeline::M2Vertex>& bind,
                               const std::vector<uint32_t>& emittedSource, bool includeAuthored=false,
                               std::set<uint16_t>* badBones=nullptr,size_t* vertexSamples=nullptr,
                               size_t* probeCount=nullptr,double* sumSq=nullptr,std::string* worst=nullptr,
                               const std::set<float>* pinnedTimes=nullptr,std::set<float>* badTimes=nullptr,
                               const SkinVisibilityPolicy* visibility=nullptr,
                               float* rawMaximum=nullptr,size_t* clippedSamples=nullptr) {
    const std::string stage=std::string(skinOracleOperation)+" anim "+std::to_string(view.sequences[sequence].animId);
    bakeProgress(stage.c_str());
    if(visibility)return skinScopedSequenceError(view,sequence,model,m2Sequence,bind,emittedSource,
        includeAuthored,badBones,vertexSamples,probeCount,sumSq,worst,pinnedTimes,badTimes,*visibility,
        rawMaximum,clippedSamples);
    std::vector<fmt::BoneMatrix> pose(view.h->boneCount);std::vector<glm::mat4> reference;
    SkinOraclePlan plan(view);std::vector<glm::vec3> source(bind.size());std::vector<uint8_t> ready(bind.size());
    std::vector<uint8_t> affected;if(badBones)affected.resize(model.bones.size());
    const auto times=skinProbeTimes(view,sequence,model,m2Sequence,includeAuthored,pinnedTimes);
    if(vertexSamples)*vertexSamples=0;
    if(probeCount)*probeCount=times.size();
    if(sumSq)*sumSq=0;
    float error=0;
    size_t timeIndex=0;bakeProgress(stage.c_str(),0,times.size());
    for(float time:times) {
        if((timeIndex++&63u)==0)bakeProgress(stage.c_str(),timeIndex-1,times.size());
        bool badTime=false;
        fmt::evaluatePose(view,sequence,time,pose.data());poseBones(model,m2Sequence,time,reference);
        plan.prepare(pose);std::fill(ready.begin(),ready.end(),0);
        size_t emitted=0;
        for(uint32_t b=0;b<view.h->batchCount;++b)
            for(uint32_t k=0;k<view.batches[b].indexCount;++k) {
                const auto& sb=view.subBatches[view.batches[b].indexStart+k];
                for(uint32_t i=0;i<sb.vertexCount;++i,++emitted) {
                    const auto vi=emittedSource[emitted];const auto& v=bind[vi];
                    if(!ready[vi]){if(!skinVertex(v,reference,source[vi],nullptr))source[vi]=v.position;ready[vi]=1;}
                    const float distance=glm::length(plan.skin(emitted)-source[vi]);
                    if(vertexSamples)++*vertexSamples;
                    if(sumSq)*sumSq+=static_cast<double>(distance)*distance;
                    if(distance>error&&worst)*worst="anim "+std::to_string(view.sequences[sequence].animId)+
                        " t="+std::to_string(time)+" vertex "+std::to_string(emittedSource[emitted]);
                    error=std::max(error,distance);
                    badTime|=distance>.02f;
                    if(distance>.02f&&badBones)
                        for(int k=0;k<4;++k)if(v.boneWeights[k]&&v.boneIndices[k]<model.bones.size())affected[v.boneIndices[k]]=1;
                }
            }
        for(const auto& attachment:model.attachments) {
            uint32_t bone=0;while(bone<view.h->boneCount&&view.bones[bone].m2Bone!=attachment.bone)++bone;
            if(bone==view.h->boneCount||attachment.bone>=reference.size())continue;
            const auto& b=pose[bone];const auto p=attachment.position;glm::vec3 actual;
            for(int r=0;r<3;++r)actual[r]=b.m[r]*p.x+b.m[3+r]*p.y+b.m[6+r]*p.z+b.m[9+r];
            const float distance=glm::length(actual-glm::vec3(reference[attachment.bone]*glm::vec4(p,1)));
            if(distance>error&&worst)*worst="point "+std::to_string(attachment.id)+" anim "+
                std::to_string(view.sequences[sequence].animId)+" t="+std::to_string(time);
            error=std::max(error,distance);if(distance>.02f&&badBones)affected[attachment.bone]=1;
            badTime|=distance>.02f;
        }
        if(badTime&&badTimes)badTimes->insert(time);
    }
    if(badBones)for(size_t b=0;b<affected.size();++b)if(affected[b])badBones->insert(static_cast<uint16_t>(b));
    bakeProgress(stage.c_str(),times.size(),times.size());
    return error;
}

// PSP: immutable-source, single-model memo. The ordinary oracle's complete
// result is reused only for byte-equal evaluated geometry, bone/key/timing
// semantics and the same complete probe-time union. Layout offsets and other
// sequences' key allocation do not affect this sequence's evaluated motion.
class SkinOracleMemo {
    struct Result {
        float error=0;size_t samples=0,probes=0;double sum=0;std::string worst;
        std::set<uint16_t> bones;std::set<float> times;
    };
    const pipeline::M2Model& model_;
    const std::vector<pipeline::M2Vertex>& bind_;
    size_t budget_,bytes_=0,computations_=0;
    using Key=std::pair<size_t,std::string>;
    struct Geometry {size_t id,charge;};
    size_t nextGeometry_=1;
    std::map<Key,Geometry> geometries_;
    std::map<Key,Result> entries_;
    struct KeyParts {std::string geometry,sequence;};
    void clear(){entries_.clear();geometries_.clear();bytes_=0;}
    template<class T> static void add(std::string& key,const T& value) {
        key.append(reinterpret_cast<const char*>(&value),sizeof(value));
    }
    static void array(std::string& key,const void* data,size_t bytes) {
        add(key,bytes);if(bytes)key.append(static_cast<const char*>(data),bytes);
    }
    KeyParts keyFor(const fmt::SkinView& v,uint32_t sequence,int sourceSequence,
                       const std::vector<uint32_t>& emitted,bool authored,const std::set<float>* pins) const {
        KeyParts parts;std::string key;const auto& h=*v.h;
        add(key,h.version);add(key,h.flags);
        add(key,h.boneCount);add(key,h.batchCount);add(key,h.subBatchCount);
        array(key,h.center,sizeof(h.center));array(key,h.half,sizeof(h.half));add(key,h.translationScale);
        array(key,v.bones,h.boneCount*sizeof(fmt::SkinBone));
        array(key,v.batches,h.batchCount*sizeof(fmt::ModelBatch));
        for(uint32_t i=0;i<h.subBatchCount;++i) {
            auto sb=v.subBatches[i];sb.vertexOffset=0;add(key,sb);
            array(key,v.vertices(v.subBatches[i]),static_cast<size_t>(sb.vertexCount)*
                fmt::skinVertexStride(sb.weights,(h.flags&fmt::kSkinFloatPosition)!=0,sb.reserved));
        }
        if(v.virtualPalettes)array(key,v.virtualPalettes,h.subBatchCount*fmt::kSkinMaxPalette*sizeof(fmt::SkinPaletteBlend));
        array(key,emitted.data(),emitted.size()*sizeof(uint32_t));parts.geometry=std::move(key);key={};
        add(key,sourceSequence);add(key,authored);add(key,v.sequences[sequence]);
        const auto* timing=v.sequenceTimes(sequence);add(key,timing!=nullptr);
        if(timing)array(key,timing,v.sequences[sequence].frameCount*sizeof(uint32_t));
        for(uint32_t b=0;b<h.boneCount;++b) {
            const auto& track=v.tracks[sequence*h.boneCount+b];
            const uint32_t refs[]={track.rotation,track.translation,track.scale};
            for(unsigned component=0;component<3;++component) {
                const auto ref=refs[component];
                if(ref==fmt::kTrackNone){add(key,ref);continue;}
                const bool compact=h.version==fmt::kSkinTimedVersion&&component==1&&(ref&fmt::kTrackCompactTranslation);
                const uint32_t encoding=(ref&fmt::kTrackAnimated)|(compact?fmt::kTrackCompactTranslation:0u);
                add(key,encoding);
                const uint32_t first=ref&~(fmt::kTrackAnimated|(compact?fmt::kTrackCompactTranslation:0u));
                size_t count=(ref&fmt::kTrackAnimated)?v.sequences[sequence].frameCount:1u;
                if((h.version>=fmt::kSkinFloatScaleVersion&&component==2)||
                   (h.version==fmt::kSkinTimedVersion&&component==1&&!compact)||
                   (component==0&&(h.flags&fmt::kSkinFloatRotation)))count*=2;
                array(key,v.blob+h.keyOffset+static_cast<size_t>(first)*8,count*8);
            }
        }
        const auto times=skinProbeTimes(v,sequence,model_,sourceSequence,authored,pins);
        add(key,times.size());for(float time:times)add(key,time);
        parts.sequence=std::move(key);return parts;
    }
    static void apply(const Result& r,std::set<uint16_t>* bones,size_t* samples,size_t* probes,double* sum,
                      std::string* worst,std::set<float>* bad) {
        if(bones)bones->insert(r.bones.begin(),r.bones.end());
        if(bad)bad->insert(r.times.begin(),r.times.end());
        if(samples)*samples=r.samples;
        if(probes)*probes=r.probes;
        if(sum)*sum=r.sum;
        if(worst&&r.error>0)*worst=r.worst;
    }
public:
    explicit SkinOracleMemo(const pipeline::M2Model& model,const std::vector<pipeline::M2Vertex>& bind,
                            size_t budget=64u*1024u*1024u):model_(model),bind_(bind),budget_(budget) {}
    size_t computations() const{return computations_;}
    size_t retainedBytes() const{return bytes_;}
    bool matchesSource(const pipeline::M2Model& model,const std::vector<pipeline::M2Vertex>& bind) const noexcept {
        return &model_ == &model && &bind_ == &bind;
    }
    float measure(const fmt::SkinView& view,uint32_t sequence,int sourceSequence,const std::vector<uint32_t>& emitted,
                  bool authored=false,std::set<uint16_t>* bones=nullptr,size_t* samples=nullptr,size_t* probes=nullptr,
                  double* sum=nullptr,std::string* worst=nullptr,const std::set<float>* pins=nullptr,std::set<float>* bad=nullptr) {
        if(!budget_) {
            ++computations_;
            return skinSequenceError(view,sequence,model_,sourceSequence,bind_,emitted,authored,bones,samples,probes,sum,worst,pins,bad);
        }
        Key key;typename std::map<Key,Geometry>::iterator geometry;
        try {
            auto parts=keyFor(view,sequence,sourceSequence,emitted,authored,pins);
            Key geometryKey{std::hash<std::string>{}(parts.geometry),std::move(parts.geometry)};
            geometry=geometries_.find(geometryKey);
            if(geometry==geometries_.end()) {
                const size_t charge=sizeof(Key)+sizeof(Geometry)+128+geometryKey.second.capacity();
                if(charge>budget_) {
                    ++computations_;
                    return skinSequenceError(view,sequence,model_,sourceSequence,bind_,emitted,authored,bones,samples,probes,sum,worst,pins,bad);
                }
                if(bytes_>budget_-charge)clear();
                // IDs remain unique while any old sequence entry is alive.
                if(nextGeometry_==std::numeric_limits<size_t>::max()){clear();nextGeometry_=1;}
                geometry=geometries_.emplace(std::move(geometryKey),Geometry{nextGeometry_++,charge}).first;
                bytes_+=charge;
            }
            key.second=std::move(parts.sequence);add(key.second,geometry->second.id);
            key.first=std::hash<std::string>{}(key.second);
            const auto found=entries_.find(key);
            if(found!=entries_.end()){apply(found->second,bones,samples,probes,sum,worst,bad);return found->second.error;}
        } catch(const std::bad_alloc&) {
            ++computations_;
            return skinSequenceError(view,sequence,model_,sourceSequence,bind_,emitted,authored,bones,samples,probes,sum,worst,pins,bad);
        }
        Result result;++computations_;
        try {
            result.error=skinSequenceError(view,sequence,model_,sourceSequence,bind_,emitted,authored,
                &result.bones,&result.samples,&result.probes,&result.sum,&result.worst,pins,&result.times);
        } catch(const std::bad_alloc&) {
            return skinSequenceError(view,sequence,model_,sourceSequence,bind_,emitted,authored,bones,samples,probes,sum,worst,pins,bad);
        }
        apply(result,bones,samples,probes,sum,worst,bad);
        // Conservative charge includes node/string/set overhead. Exceeding the
        // host-only budget or failing allocation disables reuse, never quality.
        const size_t charge=sizeof(Result)+sizeof(Key)+128+key.second.capacity()+result.worst.capacity()+
            64*(result.bones.size()+result.times.size());
        if(charge<=budget_-geometry->second.charge)try {
            if(bytes_>budget_-charge) {
                // Keep this exact geometry/ID during eviction; no stale result
                // can ever bind to a later, different geometry with that ID.
                auto keep=geometries_.extract(geometry);clear();bytes_=keep.mapped().charge;
                geometries_.insert(std::move(keep));
            }
            entries_.emplace(std::move(key),result);bytes_+=charge;
        } catch(const std::bad_alloc&) {clear();}
        return result.error;
    }
};

// PSP: only retain a denser sequence when the packed baseline actually fails
// and the extra keys measurably improve it. The full converters' existing
// animation caps therefore do not lose sequences for irrelevant refinement.
inline std::vector<uint8_t> bakeSkinned(const pipeline::M2Model& model, const std::vector<pipeline::M2Vertex>& bind,
                                       const std::vector<SkinBatchIn>& batches,
                                       const std::vector<int>& sequenceIndices, int standSeq, float rate,
                                       SkinBakeInfo& info, bool animOnly=false,
                                       const std::vector<uint16_t>* keepBones=nullptr,
                                       const std::vector<uint32_t>* frameIntervals=nullptr,
                                       size_t animationBudget=384u*1024u,
                                       const SkinVisibilityPolicy* visibility=nullptr,
                                       SkinOracleMemo* sharedMemo=nullptr) {
    SkinOracleMemo localMemo(model,bind);
    auto& memo=!visibility&&sharedMemo&&sharedMemo->matchesSource(model,bind)?*sharedMemo:localMemo;
    std::string operation="grid-check";
    auto measure=[&](const fmt::SkinView& view,uint32_t sequence,const pipeline::M2Model& source,int m2Sequence,
                     const std::vector<pipeline::M2Vertex>& vertices,const std::vector<uint32_t>& emitted,
                     bool authored=false,std::set<uint16_t>* bones=nullptr,size_t* samples=nullptr,
                     size_t* probes=nullptr,double* sum=nullptr,std::string* worst=nullptr,
                     const std::set<float>* pins=nullptr,std::set<float>* bad=nullptr) {
        SkinOracleOperationScope reporting(operation.c_str());
        if(!visibility)return memo.measure(view,sequence,m2Sequence,emitted,authored,bones,samples,probes,sum,worst,pins,bad);
        return skinSequenceError(view,sequence,source,m2Sequence,vertices,emitted,authored,bones,samples,
                                 probes,sum,worst,pins,bad,visibility);
    };
    auto probeTimes=[&](const fmt::SkinView& view,uint32_t sequence,const pipeline::M2Model& source,int m2Sequence,
                        bool authored,const std::set<float>* pins=nullptr) {
        return skinProbeTimes(view,sequence,source,m2Sequence,authored,pins,visibility);
    };
    // PSP: animationBudget is the target for the bounded frame-removal pass;
    // zero skips that pass (characters). The existing 384-KiB representation
    // heuristics still prefer compact keys/grids when they preserve accuracy.
    // The caller separately enforces its actual allocation cap.
    bakeProgress("sampling");
    auto candidate=bakeSkinnedRaw(model,bind,batches,sequenceIndices,standSeq,rate,info,false,keepBones,frameIntervals);
    fmt::SkinView dense;if(!dense.init(candidate.data(),static_cast<uint32_t>(candidate.size())))return {};
    std::vector<uint32_t> base(sequenceIndices.size()),chosen(sequenceIndices.size());bool refined=false;
    for(size_t s=0;s<sequenceIndices.size();++s) {
        const auto duration=model.sequences[sequenceIndices[s]].duration;
        base[s]=frameIntervals?(*frameIntervals)[s]:std::min<uint32_t>(65534,std::max<uint32_t>(1,static_cast<uint32_t>(std::lround(duration*rate/1000.0f))));
        chosen[s]=dense.sequences[s].frameCount-1;
        refined|=chosen[s]!=base[s];
    }
    if(refined) {
        SkinBakeInfo baseInfo;
        const auto baseline=bakeSkinnedRaw(model,bind,batches,sequenceIndices,standSeq,rate,baseInfo,false,keepBones,&base);
        fmt::SkinView original;if(!original.init(baseline.data(),static_cast<uint32_t>(baseline.size())))return {};
        for(size_t s=0;s<sequenceIndices.size();++s) {
            if(chosen[s]==base[s])continue;
            const float before=measure(original,s,model,sequenceIndices[s],bind,baseInfo.emittedSource);
            const float after=measure(dense,s,model,sequenceIndices[s],bind,info.emittedSource);
            if(before<=.02f||(after>.02f&&after>=before*.8f))chosen[s]=base[s];
        }
        // PSP: the unquantized 0.01-yard probe is deliberately conservative.
        // Prefer the smaller 60/120-Hz grid whenever the actual packed output
        // already meets 0.020; do not spend RAM chasing an unnecessary margin.
        for(uint32_t multiple:{2u,4u}) {
            auto trial=chosen;bool smaller=false;
            for(size_t s=0;s<trial.size();++s) {
                trial[s]=std::min(trial[s],base[s]*multiple);smaller|=trial[s]<chosen[s];
            }
            if(!smaller)continue;
            SkinBakeInfo trialInfo;
            const auto trialBlob=bakeSkinnedRaw(model,bind,batches,sequenceIndices,standSeq,rate,trialInfo,false,keepBones,&trial);
            fmt::SkinView probe;if(!probe.init(trialBlob.data(),static_cast<uint32_t>(trialBlob.size())))return {};
            for(size_t s=0;s<trial.size();++s)
                if(trial[s]<chosen[s]&&measure(probe,s,model,sequenceIndices[s],bind,trialInfo.emittedSource)<=.02f)
                    chosen[s]=trial[s];
        }
        candidate=bakeSkinnedRaw(model,bind,batches,sequenceIndices,standSeq,rate,info,false,keepBones,&chosen);
        if(!dense.init(candidate.data(),static_cast<uint32_t>(candidate.size())))return {};
    }
    const bool needVirtual=info.trimmedInfluences!=0;
    operation="precision-check";
    bool needPrecision=needVirtual;
    std::vector<float> errors(sequenceIndices.size());
    for(size_t s=0;s<sequenceIndices.size();++s) {
        errors[s]=measure(dense,s,model,sequenceIndices[s],bind,info.emittedSource);
        needPrecision|=errors[s]>.02f;
    }
    bool precise=false,virtuals=false,floatPositions=false;
    if(needPrecision) {
        SkinBakeInfo accurateInfo;
        auto accurate=bakeSkinnedRaw(model,bind,batches,sequenceIndices,standSeq,rate,accurateInfo,false,keepBones,&chosen,true,needVirtual);
        fmt::SkinView check;if(!check.init(accurate.data(),static_cast<uint32_t>(accurate.size())))return {};
        bool improves=needVirtual,unresolved=false;
        std::vector<float> accurateErrors(sequenceIndices.size());
        for(size_t s=0;s<sequenceIndices.size();++s)
            if(errors[s]>.02f) {
                const float after=measure(check,s,model,sequenceIndices[s],bind,accurateInfo.emittedSource);
                accurateErrors[s]=after;unresolved|=after>.02f;
                if(after<=.02f||after<errors[s]*.8f)improves=true;
            }
        if(unresolved&&!(check.h->flags&fmt::kSkinFloatPosition)) {
            SkinBakeInfo positionInfo;
            auto positionBlob=bakeSkinnedRaw(model,bind,batches,sequenceIndices,standSeq,rate,positionInfo,false,keepBones,&chosen,true,needVirtual,true);
            fmt::SkinView position;if(!position.init(positionBlob.data(),static_cast<uint32_t>(positionBlob.size())))return {};
            bool positionHelps=false;
            for(size_t s=0;s<sequenceIndices.size();++s)if(accurateErrors[s]>.02f) {
                const float after=measure(position,s,model,sequenceIndices[s],bind,positionInfo.emittedSource);
                positionHelps|=after<=.02f||after<accurateErrors[s]*.8f;
            }
            if(positionHelps){accurate=std::move(positionBlob);accurateInfo=std::move(positionInfo);floatPositions=true;improves=true;}
        }
        if(improves){candidate=std::move(accurate);info=std::move(accurateInfo);precise=true;virtuals=needVirtual;}
    }
    fmt::SkinView current;if(!current.init(candidate.data(),static_cast<uint32_t>(candidate.size())))return {};
    operation="timing-check";
    bool timingNeeded=false,realRefinement=false;
    std::vector<float> currentErrors(sequenceIndices.size());
    for(size_t s=0;s<sequenceIndices.size();++s) {
        currentErrors[s]=measure(current,s,model,sequenceIndices[s],bind,info.emittedSource,true);
        timingNeeded|=currentErrors[s]>.02f;realRefinement|=chosen[s]>base[s];
    }
    timingNeeded|=realRefinement&&info.animBytes>384u*1024u;
    // PSP: this is a host bake-work guard, not a runtime allocation limit.
    // Existing large character skeletons may already exceed the small-model
    // threshold; allow bounded refinement relative to their existing poses.
    uint64_t existingSamples=0;
    for(uint32_t s=0;s<current.h->sequenceCount;++s)
        existingSamples+=uint64_t{current.sequences[s].frameCount}*current.h->boneCount;
    const uint64_t timingSampleLimit=std::max<uint64_t>(131072,std::min<uint64_t>(1048576,2*existingSamples));
    std::vector<std::vector<uint32_t>> timedFrames;
    if(timingNeeded) {
        operation="timing-baseline";
        timedFrames.resize(sequenceIndices.size());uint64_t samples=0;
        std::vector<uint8_t> authoredGrid(sequenceIndices.size());
        for(size_t s=0;s<sequenceIndices.size();++s) {
            const uint32_t duration=model.sequences[sequenceIndices[s]].duration;
            if(!duration){timedFrames[s]={0,0};samples+=2u*current.h->boneCount;continue;}
            const bool authored=chosen[s]>base[s];
            authoredGrid[s]=authored;
            const uint32_t intervals=authored?base[s]:chosen[s];
            std::set<uint32_t> times={0,duration};
            if(!authored)for(uint32_t f=1;f<intervals;++f)
                    times.insert(static_cast<uint32_t>(std::llround(static_cast<double>(duration)*f/intervals)));
            if(authored)
                for(uint32_t b=0;b<current.h->boneCount;++b) {
                    const auto& bone=model.bones[current.bones[b].m2Bone];
                    for(const auto* track:{&bone.translation,&bone.rotation,&bone.scale}) {
                        const auto seq=sequenceIndices[s];
                        if(!skin_detail::trackVaries(*track,seq))continue;
                        for(uint32_t t:track->sequences[seq].timestamps)if(t<=duration)times.insert(t);
                    }
                }
            timedFrames[s].assign(times.begin(),times.end());samples+=uint64_t{times.size()}*current.h->boneCount;
        }
        std::vector<float> beforeErrors(sequenceIndices.size());
        for(size_t s=0;s<sequenceIndices.size();++s)
            beforeErrors[s]=measure(current,s,model,sequenceIndices[s],bind,info.emittedSource,true);
        std::vector<uint8_t> bestTimed;SkinBakeInfo bestInfo;
        std::vector<std::vector<uint32_t>> bestFrames;
        std::set<std::pair<int,uint16_t>> bestFloatTracks;
        float bestWorst=1e30f;bool bestCompact=false,bestSlerp=false,bestFloatRotation=false;
        for(int refinement=0;refinement<5 && samples<=timingSampleLimit &&
            std::all_of(timedFrames.begin(),timedFrames.end(),[](const auto& t){return t.size()<=65535;});++refinement) {
            operation="timing-refine "+std::to_string(refinement+1);bakeProgress(operation.c_str(),refinement,5);
            SkinBakeInfo timedInfo;
            auto timed=bakeSkinnedRaw(model,bind,batches,sequenceIndices,standSeq,rate,timedInfo,false,keepBones,nullptr,true,needVirtual,floatPositions,&timedFrames);
            fmt::SkinView view;if(!view.init(timed.data(),static_cast<uint32_t>(timed.size())))return {};
            bool admissible=true,helps=timedInfo.animBytes<info.animBytes&&info.animBytes>384u*1024u,usesCompact=false;
            std::vector<uint8_t> bad(sequenceIndices.size());float worst=0;
            std::set<std::pair<int,uint16_t>> floatTracks;
            for(size_t s=0;s<sequenceIndices.size();++s) {
                const float before=beforeErrors[s];
                const float after=measure(view,s,model,sequenceIndices[s],bind,timedInfo.emittedSource,true);
                bad[s]=after>.02f;worst=std::max(worst,after);
                if(before<=.02f&&after>.02f)admissible=false;
                if(before>.02f&&(after<=.02f||after<before*.8f))helps=true;
            }
            if(timedInfo.animBytes>384u*1024u) {
              for(int promotion=0;promotion<4;++promotion) {
                operation="timing-promote "+std::to_string(refinement+1)+"/"+std::to_string(promotion+1);
                SkinBakeInfo compactInfo;
                auto compact=bakeSkinnedRaw(model,bind,batches,sequenceIndices,standSeq,rate,compactInfo,false,keepBones,
                    nullptr,true,needVirtual,floatPositions,&timedFrames,true,&floatTracks);
                fmt::SkinView compactView;if(!compactView.init(compact.data(),static_cast<uint32_t>(compact.size())))return {};
                float compactWorst=0;
                std::vector<std::set<uint16_t>> affected(sequenceIndices.size());
                for(size_t s=0;s<sequenceIndices.size();++s)
                    compactWorst=std::max(compactWorst,measure(compactView,s,model,sequenceIndices[s],bind,compactInfo.emittedSource,true,&affected[s]));
                if(compactWorst<=.02f&&compactInfo.animBytes<timedInfo.animBytes) {
                    timed=std::move(compact);timedInfo=std::move(compactInfo);worst=compactWorst;
                    admissible=helps=usesCompact=true;std::fill(bad.begin(),bad.end(),0);
                    break;
                }
                if(compactWorst<=.02f)break;
                const auto previous=floatTracks.size();
                for(size_t s=0;s<sequenceIndices.size();++s)
                    for(uint16_t affectedBone:affected[s])
                        for(int b=affectedBone;b>=0&&static_cast<size_t>(b)<model.bones.size();b=model.bones[b].parentBone)
                            if(!floatTracks.emplace(sequenceIndices[s],static_cast<uint16_t>(b)).second)break;
                if(floatTracks.size()==previous)break;
              }
            }
            if(admissible&&helps&&worst<bestWorst) {
                bestTimed=std::move(timed);bestInfo=std::move(timedInfo);bestFrames=timedFrames;bestWorst=worst;
                bestCompact=usesCompact;
                bestFloatTracks=floatTracks;
            }
            if(worst<=.02f)break;
            samples=0;bool changed=false;
            for(size_t s=0;s<timedFrames.size();++s) {
                if(bad[s]) {
                    std::set<uint32_t> times;
                    if(authoredGrid[s])times.insert(timedFrames[s].begin(),timedFrames[s].end());
                    else {times.insert(0);times.insert(model.sequences[sequenceIndices[s]].duration);}
                    if(authoredGrid[s])for(size_t i=1;i<timedFrames[s].size();++i) {
                        const uint32_t lo=timedFrames[s][i-1],hi=timedFrames[s][i];
                        if(hi-lo>1)times.insert(lo+(hi-lo)/2);
                    }
                    for(uint32_t b=0;b<current.h->boneCount;++b) {
                        const auto& bone=model.bones[current.bones[b].m2Bone];
                        for(const auto* track:{&bone.translation,&bone.rotation,&bone.scale}) {
                            const auto seq=sequenceIndices[s];
                            if(!skin_detail::trackVaries(*track,seq))continue;
                            for(uint32_t t:track->sequences[seq].timestamps)if(t<=model.sequences[seq].duration)times.insert(t);
                        }
                    }
                    std::vector<uint32_t> next(times.begin(),times.end());
                    if(next==timedFrames[s]) {
                        for(size_t i=1;i<timedFrames[s].size();++i) {
                            const uint32_t lo=timedFrames[s][i-1],hi=timedFrames[s][i];if(hi-lo>1)times.insert(lo+(hi-lo)/2);
                        }
                        next.assign(times.begin(),times.end());
                    }
                    authoredGrid[s]=1;changed|=next!=timedFrames[s];timedFrames[s]=std::move(next);
                }
                samples+=uint64_t{timedFrames[s].size()}*current.h->boneCount;
            }
            if(!changed)break;
        }
        auto coarsenBest=[&]() -> bool {
            if(bestTimed.empty())return true;
            // PSP: coarsen only an over-budget timed candidate, retaining the
            // original strong oracle's anchors. Restore removed frames around
            // failures; never buy memory by dropping sequences or tolerance.
            std::vector<std::set<float>> pinnedTimes=bestInfo.verificationTimes;
            if(animationBudget&&bestInfo.animBytes>animationBudget) {
                fmt::SkinView original;
                if(!original.init(bestTimed.data(),static_cast<uint32_t>(bestTimed.size())))return false;
                pinnedTimes.resize(sequenceIndices.size());
                for(size_t s=0;s<sequenceIndices.size();++s)
                    pinnedTimes[s]=probeTimes(original,s,model,sequenceIndices[s],true,&pinnedTimes[s]);
                const auto originalTimed=bestTimed;const auto originalInfo=bestInfo;
                const auto originalFrames=bestFrames;
                std::vector<size_t> order(sequenceIndices.size());std::iota(order.begin(),order.end(),0);
                std::sort(order.begin(),order.end(),[&](size_t a,size_t b){return bestFrames[a].size()>bestFrames[b].size();});
                for(unsigned phase=0;phase<6&&bestInfo.animBytes>animationBudget;++phase)for(size_t s:order) {
                    operation="coarsen "+std::to_string(phase+1);bakeProgress(operation.c_str(),s,order.size());
                    if(bestInfo.animBytes<=animationBudget)break;
                    const auto full=bestFrames[s];if(full.size()<4)continue;
                    fmt::SkinView aliases;
                    if(!aliases.init(bestTimed.data(),static_cast<uint32_t>(bestTimed.size())))return false;
                    // Identical stored rows share key bytes. Coarsen their grids
                    // together so an individually smaller track cannot break
                    // deduplication and increase total allocation.
                    std::vector<size_t> group;
                    for(size_t other=0;other<sequenceIndices.size();++other)
                        if(bestFrames[other]==full&&aliases.sequences[other].durationMs==aliases.sequences[s].durationMs&&
                           !std::memcmp(aliases.tracks+other*aliases.h->boneCount,aliases.tracks+s*aliases.h->boneCount,
                                        aliases.h->boneCount*sizeof(fmt::BoneTrack)))group.push_back(other);
                    auto trialFrames=bestFrames;trialFrames[s].clear();trialFrames[s].push_back(full.front());
                    for(size_t k=1;k+1<full.size();++k)
                        if(phase%3==0?k%2==0:phase%3==1?k%2!=0:k%3!=1)trialFrames[s].push_back(full[k]);
                    trialFrames[s].push_back(full.back());
                    for(unsigned refinement=0;refinement<5;++refinement) {
                        for(size_t alias:group)trialFrames[alias]=trialFrames[s];
                        SkinBakeInfo trialInfo;
                        auto trial=bakeSkinnedRaw(model,bind,batches,sequenceIndices,standSeq,rate,trialInfo,false,keepBones,
                            nullptr,true,needVirtual,floatPositions,&trialFrames,bestCompact,&bestFloatTracks,bestSlerp,bestFloatRotation);
                        fmt::SkinView test;if(!test.init(trial.data(),static_cast<uint32_t>(trial.size())))return false;
                        std::set<float> bad;float error=0;
                        for(size_t alias:group)
                            error=std::max(error,measure(test,alias,model,sequenceIndices[alias],bind,
                                trialInfo.emittedSource,true,nullptr,nullptr,nullptr,nullptr,nullptr,&pinnedTimes[alias],&bad));
                        if(error<=.02f) {
                            if(trialInfo.animBytes<bestInfo.animBytes) {
                                bestTimed=std::move(trial);bestInfo=std::move(trialInfo);bestFrames=std::move(trialFrames);
                            }
                            break;
                        }
                        std::set<uint32_t> restored(trialFrames[s].begin(),trialFrames[s].end());
                        for(float t:bad) {
                            auto hi=std::upper_bound(trialFrames[s].begin(),trialFrames[s].end(),t);
                            if(hi==trialFrames[s].end()||hi==trialFrames[s].begin())continue;
                            const auto lo=hi-1;
                            for(auto at=std::upper_bound(full.begin(),full.end(),*lo);at!=full.end()&&*at<*hi;++at)restored.insert(*at);
                        }
                        if(restored.size()==trialFrames[s].size()||restored.size()==full.size())break;
                        trialFrames[s].assign(restored.begin(),restored.end());
                    }
                }
                fmt::SkinView finalView;if(!finalView.init(bestTimed.data(),static_cast<uint32_t>(bestTimed.size())))return false;
                float error=0;
                for(size_t s=0;s<sequenceIndices.size();++s)
                    error=std::max(error,measure(finalView,s,model,sequenceIndices[s],bind,
                        bestInfo.emittedSource,true,nullptr,nullptr,nullptr,nullptr,nullptr,&pinnedTimes[s]));
                if(error>.02f){bestTimed=originalTimed;bestInfo=originalInfo;bestFrames=originalFrames;}else bestWorst=error;
                bestInfo.verificationTimes=pinnedTimes;
            }
            return true;
        };
        if(!coarsenBest())return {};
        // PSP: rare fallback for a remaining precision floor or a timed model
        // that cannot meet its byte budget with dense NLERP samples. Preserve
        // source corners and use source-style SLERP rather than more samples.
        if(bestTimed.empty()||bestWorst>.02f||(animationBudget&&bestInfo.animBytes>animationBudget)) {
            fmt::SkinView previous;
            const auto& previousBlob=bestTimed.empty()?candidate:bestTimed;
            if(!previous.init(previousBlob.data(),static_cast<uint32_t>(previousBlob.size())))return {};
            std::vector<std::set<float>> pins(sequenceIndices.size());
            std::vector<std::vector<uint32_t>> authored(sequenceIndices.size());uint64_t authoredSamples=0;
            for(size_t s=0;s<sequenceIndices.size();++s) {
                pins[s]=probeTimes(previous,s,model,sequenceIndices[s],true,
                    !bestTimed.empty()&&s<bestInfo.verificationTimes.size()?&bestInfo.verificationTimes[s]:nullptr);
                const auto duration=model.sequences[sequenceIndices[s]].duration;
                std::set<uint32_t> times{0,duration};
                for(uint32_t b=0;b<current.h->boneCount;++b) {
                    const auto& bone=model.bones[current.bones[b].m2Bone];
                    for(const auto* track:{&bone.translation,&bone.rotation,&bone.scale})
                        if(skin_detail::trackVaries(*track,sequenceIndices[s]))
                            for(uint32_t t:track->sequences[sequenceIndices[s]].timestamps)if(t<=duration)times.insert(t);
                }
                authored[s].assign(times.begin(),times.end());if(!duration)authored[s]={0,0};
                authoredSamples+=uint64_t{authored[s].size()}*current.h->boneCount;
            }
            if(authoredSamples<=timingSampleLimit&&std::all_of(authored.begin(),authored.end(),[](const auto& t){return t.size()<=65535;}))
            for(unsigned rotationPrecision=0;rotationPrecision<2;++rotationPrecision) {
                operation="slerp-check "+std::to_string(rotationPrecision+1);
                auto measureExact=[&](const std::vector<uint8_t>& bytes,const SkinBakeInfo& stats,std::vector<std::set<uint16_t>>* affected=nullptr) {
                    fmt::SkinView view;if(!view.init(bytes.data(),static_cast<uint32_t>(bytes.size())))return 1e30f;
                    float worst=0;
                    for(size_t s=0;s<sequenceIndices.size();++s)
                        worst=std::max(worst,measure(view,s,model,sequenceIndices[s],bind,stats.emittedSource,
                            true,affected?&(*affected)[s]:nullptr,nullptr,nullptr,nullptr,nullptr,&pins[s]));
                    return worst;
                };
                SkinBakeInfo exactInfo;
                auto exact=bakeSkinnedRaw(model,bind,batches,sequenceIndices,standSeq,rate,exactInfo,false,keepBones,
                    nullptr,true,needVirtual,floatPositions,&authored,false,nullptr,true,rotationPrecision!=0);
                float worst=measureExact(exact,exactInfo);if(worst>.02f)continue;
                bool compactSelected=false;std::set<std::pair<int,uint16_t>> floatTracks;
                for(unsigned promotion=0;promotion<4;++promotion) {
                    SkinBakeInfo compactInfo;
                    auto compact=bakeSkinnedRaw(model,bind,batches,sequenceIndices,standSeq,rate,compactInfo,false,keepBones,
                        nullptr,true,needVirtual,floatPositions,&authored,true,&floatTracks,true,rotationPrecision!=0);
                    std::vector<std::set<uint16_t>> affected(sequenceIndices.size());
                    const float error=measureExact(compact,compactInfo,&affected);
                    if(error<=.02f) {
                        if(compactInfo.animBytes<exactInfo.animBytes){exact=std::move(compact);exactInfo=std::move(compactInfo);worst=error;compactSelected=true;}
                        break;
                    }
                    const auto previousCount=floatTracks.size();
                    for(size_t s=0;s<sequenceIndices.size();++s)for(uint16_t affectedBone:affected[s])
                        for(int b=affectedBone;b>=0&&static_cast<size_t>(b)<model.bones.size();b=model.bones[b].parentBone)
                            if(!floatTracks.emplace(sequenceIndices[s],static_cast<uint16_t>(b)).second)break;
                    if(floatTracks.size()==previousCount)break;
                }
                if(bestTimed.empty()||bestWorst>.02f||exactInfo.animBytes<bestInfo.animBytes) {
                    exactInfo.verificationTimes=pins;bestTimed=std::move(exact);bestInfo=std::move(exactInfo);
                    bestFrames=authored;bestWorst=worst;bestCompact=compactSelected;bestFloatTracks=std::move(floatTracks);
                    bestSlerp=true;bestFloatRotation=rotationPrecision!=0;
                }
                break; // the smaller int16 mode wins whenever it already passes
            }
        }
        if(!bestTimed.empty()) {
            if(bestSlerp&&!coarsenBest())return {};
            auto pinnedTimes=bestInfo.verificationTimes;
            info=std::move(bestInfo);
            if(animOnly) {
                auto animation=bakeSkinnedRaw(model,bind,batches,sequenceIndices,standSeq,rate,info,true,keepBones,nullptr,true,needVirtual,floatPositions,&bestFrames,bestCompact,&bestFloatTracks,bestSlerp,bestFloatRotation);
                info.verificationTimes=std::move(pinnedTimes);
                return animation;
            }
            return bestTimed;
        }
    }
    if(animOnly)return bakeSkinnedRaw(model,bind,batches,sequenceIndices,standSeq,rate,info,true,keepBones,&chosen,precise,virtuals,floatPositions);
    return candidate;
}

inline SkinVerifyResult verifySkinned(const std::vector<uint8_t>& blob, const pipeline::M2Model& model,
                                      const std::vector<pipeline::M2Vertex>& bind,
                                      const std::vector<int>& sequenceIndices, const SkinBakeInfo& info,
                                      const std::vector<SkinBatchIn>& batches, std::vector<uint8_t>* dump,
                                      const std::set<uint16_t>* dumpGeosets,
                                      const SkinVisibilityPolicy* visibility=nullptr) {
    SkinVerifyResult r;
    fmt::SkinView view;
    if (!view.init(blob.data(), static_cast<uint32_t>(blob.size()))) {
        r.maxErr = 1e9;
        r.worst = "blob rejected by SkinView::init";
        return r;
    }
    if(visibility) {
        if(sequenceIndices.size()!=view.h->sequenceCount){r.maxErr=1e30f;r.worst="sequence count mismatch";return r;}
        for(uint32_t s=0;s<view.h->sequenceCount;++s) {
            size_t samples=0,probes=0,clipped=0;double sum=0;float raw=0;std::string where;
            const auto* pins=s<info.verificationTimes.size()?&info.verificationTimes[s]:nullptr;
            const float error=skinSequenceError(view,s,model,sequenceIndices[s],bind,info.emittedSource,true,
                nullptr,&samples,&probes,&sum,&where,pins,nullptr,visibility,&raw,&clipped);
            r.samples+=samples;r.probeTimes+=probes;r.sumSq+=sum;r.clippedSamples+=clipped;
            r.rawMaxErr=std::max(r.rawMaxErr,raw);
            if(error>r.maxErr){r.maxErr=error;r.worst=where;}
        }
        return r;
    }
    std::vector<fmt::BoneMatrix> pose(view.h->boneCount);
    std::vector<glm::mat4> ref;
    SkinOraclePlan plan(view);std::vector<glm::vec3> source(bind.size());std::vector<uint8_t> ready(bind.size());
    for (int s = 0; s < view.h->sequenceCount; ++s) {
        const int si = sequenceIndices[s];
        const float dur = static_cast<float>(view.sequences[s].durationMs);
        const auto* pinned=static_cast<size_t>(s)<info.verificationTimes.size()?&info.verificationTimes[s]:nullptr;
        const auto times=skinProbeTimes(view,static_cast<uint32_t>(s),model,si,true,pinned);
        const std::string stage="verify anim "+std::to_string(view.sequences[s].animId);size_t timeIndex=0;
        bakeProgress(stage.c_str(),0,times.size());
        for (float t : times) {
            if((timeIndex++&63u)==0)bakeProgress(stage.c_str(),timeIndex-1,times.size());
            ++r.probeTimes;
            fmt::evaluatePose(view, s, t, pose.data());
            poseBones(model, si, t, ref);
            plan.prepare(pose);std::fill(ready.begin(),ready.end(),0);
            size_t e = 0;
            double seqMax = 0.0;
            for (int b = 0; b < view.h->batchCount; ++b) {
                const fmt::ModelBatch& mb = view.batches[b];
                const bool dumpIt = dump && t == dur * 0.37f && (!dumpGeosets || dumpGeosets->count(view.geosets[b]));
                for (uint32_t k = 0; k < mb.indexCount; ++k) {
                    const fmt::SkinSubBatch& sb = view.subBatches[mb.indexStart + k];
                    std::vector<glm::vec3> ours(sb.vertexCount);
                    for (uint32_t i = 0; i < sb.vertexCount; ++i, ++e) {
                        ours[i] = plan.skin(e);const auto vi=info.emittedSource[e];
                        if(!ready[vi]){if(!skinVertex(bind[vi],ref,source[vi],nullptr))source[vi]=bind[vi].position;ready[vi]=1;}
                        const double err = glm::length(ours[i] - source[vi]);
                        r.sumSq += err * err;
                        ++r.samples;
                        seqMax = std::max(seqMax, err);
                        if (err > r.maxErr) {
                            r.maxErr = err;
                            char w[160];
                            std::snprintf(w, sizeof(w), "anim %u t=%.0f ms vertex %u: %.4f yd", view.sequences[s].animId,
                                          t, info.emittedSource[e], err);
                            r.worst = w;
                        }
                    }
                    if (dumpIt)
                        for (uint32_t i = 2; i < sb.vertexCount; ++i)
                            if (ours[i] != ours[i - 1] && ours[i] != ours[i - 2] && ours[i - 1] != ours[i - 2])
                                appendTri(*dump, ours[i - 2], ours[i - 1], ours[i], static_cast<uint16_t>(s * 2 + 1));
                }
                // Reference triangles of the same batch (source mesh, CPU skinning).
                if (dumpIt) {
                    const auto& tris = batches[b].tris;
                    for (size_t t = 0; t + 2 < tris.size(); t += 3) {
                        glm::vec3 p[3];
                        for (int c = 0; c < 3; ++c)
                            if (!skinVertex(bind[tris[t + c]], ref, p[c], nullptr)) p[c] = bind[tris[t + c]].position;
                        appendTri(*dump, p[0], p[1], p[2], static_cast<uint16_t>(s * 2));
                    }
                }
            }
            if (g_verbose) std::printf("    anim %u t=%.0f: max %.4f\n", view.sequences[s].animId, t, seqMax);
        }
    }
    return r;
}

} // namespace wowee::pspconv
