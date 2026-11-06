// PSP: host-only qualification for a source-specific renderer-envelope proof.
#pragma once
#include <array>
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace wowee::pspconv {
struct ScopedSkinSource {std::string model;std::array<std::string,4> skins;};
struct ScopedSkinContext {
    bool completeNpcAliases=false,completeGameObjectAliases=false;
    bool hasHumanoidAlias=false,hasGameObjectAlias=false,hasSpawnInput=false;
    std::vector<float> aliasScales;
    double consumerEnvelope=0;
    size_t requestedAnimationBudget=384u*1024u;
};
struct SkinVisibilityPolicy {double envelope=1000000;};
namespace skin_source_detail {
struct State {
    uint32_t h[8] = {0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
                    0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19};
    uint64_t totalBits = 0;
    uint8_t buf[64] = {};
    size_t bufLen = 0;
};
static inline uint32_t rotr(uint32_t x, uint32_t n) { return (x >> n) | (x << (32 - n)); }
static void compress(State& s, const uint8_t* block) {
    static const uint32_t K[64] = {
        0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
        0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
        0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
        0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
        0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
        0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
        0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
        0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2,
    };
    uint32_t w[64];
    for (int i = 0; i < 16; ++i) {
        w[i] = (uint32_t(block[i*4]) << 24) | (uint32_t(block[i*4+1]) << 16) |
               (uint32_t(block[i*4+2]) << 8) | uint32_t(block[i*4+3]);
    }
    for (int i = 16; i < 64; ++i) {
        uint32_t s0 = rotr(w[i-15], 7) ^ rotr(w[i-15], 18) ^ (w[i-15] >> 3);
        uint32_t s1 = rotr(w[i-2], 17) ^ rotr(w[i-2], 19) ^ (w[i-2] >> 10);
        w[i] = w[i-16] + s0 + w[i-7] + s1;
    }
    uint32_t a = s.h[0], b = s.h[1], c = s.h[2], d = s.h[3];
    uint32_t e = s.h[4], f = s.h[5], g = s.h[6], h = s.h[7];
    for (int i = 0; i < 64; ++i) {
        uint32_t S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
        uint32_t ch = (e & f) ^ (~e & g);
        uint32_t t1 = h + S1 + ch + K[i] + w[i];
        uint32_t S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
        uint32_t mj = (a & b) ^ (a & c) ^ (b & c);
        uint32_t t2 = S0 + mj;
        h = g; g = f; f = e; e = d + t1;
        d = c; c = b; b = a; a = t1 + t2;
    }
    s.h[0] += a; s.h[1] += b; s.h[2] += c; s.h[3] += d;
    s.h[4] += e; s.h[5] += f; s.h[6] += g; s.h[7] += h;
}
static void update(State& s, const uint8_t* data, size_t len) {
    s.totalBits += len * 8;
    while (len > 0) {
        size_t take = std::min(len, sizeof(s.buf) - s.bufLen);
        std::memcpy(s.buf + s.bufLen, data, take);
        s.bufLen += take; data += take; len -= take;
        if (s.bufLen == 64) { compress(s, s.buf); s.bufLen = 0; }
    }
}
static std::string hexFinal(State& s) {
    s.buf[s.bufLen++] = 0x80;
    if (s.bufLen > 56) {
        std::memset(s.buf + s.bufLen, 0, 64 - s.bufLen);
        compress(s, s.buf); s.bufLen = 0;
    }
    std::memset(s.buf + s.bufLen, 0, 56 - s.bufLen);
    for (int i = 7; i >= 0; --i) s.buf[56 + (7 - i)] = (s.totalBits >> (i * 8)) & 0xFF;
    compress(s, s.buf);
    char out[65] = {};
    for (int i = 0; i < 8; ++i) {
        std::snprintf(out + i * 8, 9, "%08x", s.h[i]);
    }
    return std::string(out);
}
} // namespace skin_source_detail
inline std::string skinSourceSha256(const std::vector<uint8_t>& bytes) {
    skin_source_detail::State state;
    skin_source_detail::update(state,bytes.data(),bytes.size());
    return skin_source_detail::hexFinal(state);
}
// PSP: neutral byte signatures qualify one audited source variant, never its
// planner-dependent n_ ID. These are facts, not embedded client assets.
inline bool scopedSkinModelMatches(const std::vector<uint8_t>& model) {
    return model.size()==799024&&skinSourceSha256(model)==
        "ac4d1f4220cd7c416b890d379ef7f53068151c6f9cf1a32e50a68457dc4366d5";
}
inline bool scopedSkinSourceMatches(const ScopedSkinSource& source) {
    const ScopedSkinSource accepted{
        "ac4d1f4220cd7c416b890d379ef7f53068151c6f9cf1a32e50a68457dc4366d5",
        {"cdd13a1e4cd9791de4a7bb1d3b66ebb3ed86d33069e17423fb0116e5bf60d7ad",
         "04cce2c2ee618a94233f1cb1ce7c36aa69d97bb01f8bd5ab68fb21cf01a97236","",""}};
    return source.model==accepted.model&&source.skins==accepted.skins;
}
inline bool qualifyScopedSkin(const ScopedSkinSource& source,const ScopedSkinContext& context) {
    if(!scopedSkinSourceMatches(source)||!context.completeNpcAliases||!context.completeGameObjectAliases||
       context.aliasScales.empty()||context.hasHumanoidAlias||context.hasGameObjectAlias||
       context.hasSpawnInput||!std::isfinite(context.consumerEnvelope)||context.consumerEnvelope<=0||
       context.consumerEnvelope>=1000000||context.requestedAnimationBudget!=384u*1024u)return false;
    for(float scale:context.aliasScales)if(!std::isfinite(scale)||scale!=1.0f)return false;
    return true;
}
inline bool scopedSkinResultFits(size_t wanted,size_t retained,size_t animationBytes,size_t skinBytes,
                                  float visibleError,float pointError) {
    return wanted>0&&wanted==retained&&animationBytes<=600u*1024u&&skinBytes<2u*1024u*1024u&&
        std::isfinite(visibleError)&&std::isfinite(pointError)&&visibleError<=.02f&&pointError<=.02f;
}
} // namespace wowee::pspconv
