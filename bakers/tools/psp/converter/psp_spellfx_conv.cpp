// psp_spellfx_conv - spell effects for the PSP.
//
//   psp_spellfx_conv --extracted <dir> --out <psp/bin/data/spellfx.pak>
//                    [--all] [--spells <file>] [--tex 64] [--fps 15] [--max-frames 48]
//                    [--max-emitters 4] [--list] [--list-missing <file>]
//   psp_spellfx_conv --verify <spellfx.pak>
//
// Which visuals: by default those of the players' class spells - every Spell.dbc row whose
// SkillLineAbility line has SkillLine.Category 7 (class and racial lines) - plus the
// Hearthstone (8690) and the spell ids listed in --spells (one per line); --all bakes every
// SpellVisual a spell names. Profession / secondary lines (categories 11 / 9) are what
// upstream's isProfessionSpell skips anyway.
//
// The visual -> effect path chain is upstream SpellVisualSystem::loadSpellVisualDbc
// unchanged (include/rendering/psp/psp_spellfx_format.hpp); each distinct path is one
// effect "e_<n>", baked from the M2 by upstream's M2Loader with upstream M2Renderer's rules
// for a spell effect (see that header for what the PSP keeps and what it simplifies).
// Textures: BLP -> WTEX T8 (model_bake.hpp TextureBank), at most --tex texels a side.
// --list prints every effect (size, vertices, groups, emitters) and the totals;
// --list-missing writes the game paths that were not in --extracted (psp_mpq_extract --names).
// The M2 -> WSFX bake moved to particle_bake.hpp (bakeFx, shared with
// the world's emitters in psp_model_conv), now with the ribbon emitters (WSFX v2; a spell effect
// without ribbons stays v1) - --no-ribbons bakes as before.
#include "conv_common.hpp"
#include "model_bake.hpp"
#include "particle_bake.hpp"

#include "pipeline/dbc_loader.hpp"
#include "pipeline/m2_loader.hpp"
#include "rendering/m2_track_sampler.hpp"
#include "rendering/psp/psp_spellfx_format.hpp"

#include <glm/glm.hpp>
#include <glm/gtc/quaternion.hpp>
#include <meshoptimizer.h>

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <map>
#include <set>
#include <string>
#include <unordered_map>
#include <vector>

using namespace wowee;
using namespace wowee::pspconv;
namespace sfx = wowee::rendering::psp;

namespace {

std::string gRoot;
std::set<std::string> gMissing;  // game paths (as named), for psp_mpq_extract --names

struct Options {
    std::string out, spellsFile, listMissing;
    bool all = false, list = false;
    int tex = 64;
    float fps = 15.0f;
    int maxFrames = 48;
    int maxEmitters = sfx::kFxSpellEmitters;  // kFxMaxEmitters grew to 16 for world models
    bool ribbons = true;  // --no-ribbons
};

bool readGame(const std::string& gamePath, std::vector<uint8_t>& out) {
    if (readFile(gRoot + "/" + lower(gamePath), out)) return true;
    gMissing.insert(gamePath);
    return false;
}

bool loadDbc(const char* name, pipeline::DBCFile& d) {
    std::vector<uint8_t> b;
    if (!readFile(gRoot + "/dbfilesclient/" + name, b) || !d.load(b)) {
        std::fprintf(stderr, "cannot read %s/dbfilesclient/%s\n", gRoot.c_str(), name);
        return false;
    }
    return true;
}

using EffectStats = FxBakeStats;  // the bake is particle_bake.hpp's bakeFx

bool bakeEffect(const std::string& m2Path, TextureBank& bank, const Options& o, std::vector<uint8_t>& blob,
                EffectStats& st) {
    st.path = m2Path;
    st.attach = classifyFxAttachment(m2Path);
    std::vector<uint8_t> m2Bytes;
    if (!readGame(m2Path, m2Bytes)) {
        st.why = "missing";
        return false;
    }
    pipeline::M2Model m = pipeline::M2Loader::load(m2Bytes);
    if (m.version >= 264) {
        std::vector<uint8_t> skin;
        if (readGame(pipeline::skinPathForM2(m2Path), skin)) pipeline::M2Loader::loadSkin(skin, m);
    }
    FxBakeOptions fo;
    fo.fps = o.fps;
    fo.maxFrames = o.maxFrames;
    fo.maxEmitters = o.maxEmitters;
    fo.ribbons = o.ribbons;
    fo.mesh = true;
    fo.cls = sfx::kPtcSpell;
    fo.attach = st.attach;
    return bakeFx(m2Path, m2Bytes, m, fo, bank, readGame, gMissing, blob, st);
}

// --verify: every "e_" valid, every texture id an "x_", every "vis" row on a baked effect.
int verifyPak(const std::string& path) {
    std::vector<uint8_t> d;
    if (!readFile(path, d) || d.size() < 16 || std::memcmp(d.data(), "WPAK", 4) != 0) {
        std::fprintf(stderr, "verify: cannot read %s\n", path.c_str());
        return 2;
    }
    uint32_t count, tocOff;
    std::memcpy(&count, d.data() + 8, 4);
    std::memcpy(&tocOff, d.data() + 12, 4);
    std::map<std::string, std::pair<uint32_t, uint32_t>> ent;
    for (uint32_t i = 0; i < count; ++i) {
        const uint8_t* r = d.data() + tocOff + i * 64;
        char nm[49] = {};
        std::memcpy(nm, r, 48);
        uint32_t off, size;
        std::memcpy(&off, r + 48, 4);
        std::memcpy(&size, r + 52, 4);
        if (static_cast<uint64_t>(off) + size > d.size()) {
            std::fprintf(stderr, "verify: %s out of the file\n", nm);
            return 1;
        }
        ent[nm] = {off, size};
    }
    int errors = 0;
    auto vis = ent.find("vis");
    if (vis == ent.end() || vis->second.second < sizeof(sfx::SpellFxVisualTable)) {
        std::fprintf(stderr, "verify: no vis\n");
        return 1;
    }
    sfx::SpellFxVisualTable vt;
    std::memcpy(&vt, d.data() + vis->second.first, sizeof(vt));
    if (std::memcmp(vt.magic, "WSFV", 4) != 0 || vt.version != sfx::kSpellFxVersion ||
        vis->second.second < sizeof(vt) + vt.count * sizeof(sfx::SpellFxVisual)) {
        std::fprintf(stderr, "verify: vis header bad\n");
        return 1;
    }
    size_t effectBytes = 0, texBytes = 0, maxEffect = 0;
    for (uint32_t n = 0; n < vt.effectCount; ++n) {
        auto it = ent.find("e_" + std::to_string(n));
        if (it == ent.end()) {
            ++errors;
            std::fprintf(stderr, "verify: e_%u missing\n", n);
            continue;
        }
        const uint8_t* b = d.data() + it->second.first;
        if (!sfx::spellFxValid(b, it->second.second)) {
            ++errors;
            std::fprintf(stderr, "verify: e_%u invalid\n", n);
            continue;
        }
        effectBytes += it->second.second;
        maxEffect = std::max<size_t>(maxEffect, it->second.second);
        sfx::SpellFxHeader h;
        std::memcpy(&h, b, sizeof(h));
        for (uint32_t t = 0; t < h.textureCount; ++t) {
            uint16_t id;
            std::memcpy(&id, b + h.textureOffset + t * 2, 2);
            if (id >= vt.textureCount || !ent.count("x_" + std::to_string(id))) {
                ++errors;
                std::fprintf(stderr, "verify: e_%u texture %u missing\n", n, id);
            }
        }
    }
    for (uint32_t t = 0; t < vt.textureCount; ++t) {
        auto it = ent.find("x_" + std::to_string(t));
        if (it == ent.end() || it->second.second < 32 || std::memcmp(d.data() + it->second.first, "WTEX", 4) != 0) {
            ++errors;
            std::fprintf(stderr, "verify: x_%u missing or not WTEX\n", t);
        } else {
            texBytes += it->second.second;
        }
    }
    uint32_t prev = 0;
    for (uint32_t i = 0; i < vt.count; ++i) {
        sfx::SpellFxVisual v;
        std::memcpy(&v, d.data() + vis->second.first + sizeof(vt) + i * sizeof(v), sizeof(v));
        if (i && v.visual <= prev) ++errors;
        prev = v.visual;
        for (uint16_t e : {v.precast, v.cast, v.impact})
            if (e != sfx::kFxNone && e >= vt.effectCount) ++errors;
    }
    std::printf("verify %s: visuals=%u effects=%u (%zu KB, largest %zu KB) textures=%u (%zu KB) errors=%d\n",
                path.c_str(), vt.count, vt.effectCount, effectBytes / 1024, maxEffect / 1024, vt.textureCount,
                texBytes / 1024, errors);
    return errors ? 1 : 0;
}

}  // namespace

int main(int argc, char** argv) {
    Options o;
    std::string verify;
    for (int i = 1; i < argc; ++i) {
        const std::string a = argv[i];
        auto next = [&]() -> std::string { return i + 1 < argc ? argv[++i] : std::string(); };
        if (a == "--extracted") gRoot = next();
        else if (a == "--out") o.out = next();
        else if (a == "--all") o.all = true;
        else if (a == "--spells") o.spellsFile = next();
        else if (a == "--tex") o.tex = std::clamp(std::atoi(next().c_str()), 16, 256);
        else if (a == "--fps") o.fps = std::clamp(static_cast<float>(std::atof(next().c_str())), 2.0f, 60.0f);
        else if (a == "--max-frames") o.maxFrames = std::clamp(std::atoi(next().c_str()), 2, 256);
        else if (a == "--max-emitters") o.maxEmitters = std::clamp(std::atoi(next().c_str()), 0, sfx::kFxMaxEmitters);
        else if (a == "--no-ribbons") o.ribbons = false;  // bakes as before (WSFX v1)
        else if (a == "--list") o.list = true;
        else if (a == "--list-missing") o.listMissing = next();
        else if (a == "--verify") verify = next();
        else {
            std::fprintf(stderr,
                         "usage: psp_spellfx_conv --extracted <dir> --out <spellfx.pak> [--all] [--spells <file>] "
                         "[--tex 64] [--fps 15] [--max-frames 48] [--max-emitters 4] [--no-ribbons] [--list]"
                         " [--list-missing <file>]\n"
                         "       psp_spellfx_conv --verify <spellfx.pak>\n");
            return 2;
        }
    }
    if (!verify.empty()) return verifyPak(verify);
    if (gRoot.empty() || o.out.empty()) {
        std::fprintf(stderr, "--extracted and --out are required\n");
        return 2;
    }

    pipeline::DBCFile spell, sla, sl, sv, kit, fx;
    if (!loadDbc("spell.dbc", spell) || !loadDbc("skilllineability.dbc", sla) || !loadDbc("skillline.dbc", sl) ||
        !loadDbc("spellvisual.dbc", sv) || !loadDbc("spellvisualkit.dbc", kit) ||
        !loadDbc("spellvisualeffectname.dbc", fx))
        return 2;

    // Data/expansions/wotlk/dbc_layouts.json (upstream's fallbacks are the same numbers).
    constexpr uint32_t kSpellVisualField = 131;                       // Spell.SpellVisualID
    constexpr uint32_t kSlaLine = 1, kSlaSpell = 2, kSlCategory = 1;  // SkillLineAbility / SkillLine
    constexpr uint32_t kSvPrecast = 1, kSvCast = 2, kSvImpact = 3, kSvMissile = 8;
    constexpr uint32_t kFxPath = 2;
    static constexpr uint32_t kKitFields[] = {11, 5, 6, 7, 4, 3, 8, 12, 13};

    // Which spells.
    std::set<uint32_t> spells;
    {
        std::unordered_map<uint32_t, uint32_t> lineCategory;
        for (uint32_t i = 0; i < sl.getRecordCount(); ++i) lineCategory[sl.getUInt32(i, 0)] = sl.getUInt32(i, kSlCategory);
        for (uint32_t i = 0; i < sla.getRecordCount(); ++i) {
            auto it = lineCategory.find(sla.getUInt32(i, kSlaLine));
            if (it != lineCategory.end() && it->second == 7) spells.insert(sla.getUInt32(i, kSlaSpell));
        }
        spells.insert(8690);  // Hearthstone
        if (!o.spellsFile.empty()) {
            std::ifstream f(o.spellsFile);
            uint32_t id;
            while (f >> id) spells.insert(id);
        }
    }
    std::set<uint32_t> visuals;
    for (uint32_t i = 0; i < spell.getRecordCount(); ++i) {
        const uint32_t v = spell.getUInt32(i, kSpellVisualField);
        if (v && (o.all || spells.count(spell.getUInt32(i, 0)))) visuals.insert(v);
    }

    // upstream loadSpellVisualDbc: effect name id -> path, kit -> first effect, visual -> paths.
    std::unordered_map<uint32_t, std::string> effectPaths;
    for (uint32_t i = 0; i < fx.getRecordCount(); ++i) {
        const uint32_t id = fx.getUInt32(i, 0);
        std::string p = fx.getString(i, kFxPath);
        if (id && !p.empty()) effectPaths[id] = pipeline::modelPathToM2(p);
    }
    std::unordered_map<uint32_t, uint32_t> kitEffect;
    for (uint32_t i = 0; i < kit.getRecordCount(); ++i) {
        const uint32_t kid = kit.getUInt32(i, 0);
        if (!kid) continue;
        uint32_t eff = 0;
        for (uint32_t f : kKitFields)
            if (!eff && f < kit.getFieldCount()) eff = kit.getUInt32(i, f);
        if (eff) kitEffect[kid] = eff;
    }
    auto kitPath = [&](uint32_t kid) -> std::string {
        auto k = kitEffect.find(kid);
        if (!kid || k == kitEffect.end()) return {};
        auto p = effectPaths.find(k->second);
        return p != effectPaths.end() ? p->second : std::string{};
    };
    auto missilePath = [&](uint32_t eid) -> std::string {
        auto p = effectPaths.find(eid);
        return (eid && p != effectPaths.end()) ? p->second : std::string{};
    };

    std::vector<PakItem> items;
    TextureBank bank(gRoot, o.tex, items);
    std::map<std::string, int> effectOf;  // lower path -> effect index (-1 = failed)
    std::vector<EffectStats> stats;
    std::map<std::string, int> failures;
    int nextEffect = 0;
    auto effectFor = [&](const std::string& path) -> uint16_t {
        if (path.empty()) return sfx::kFxNone;
        const std::string key = lower(path);
        auto it = effectOf.find(key);
        if (it != effectOf.end()) return it->second < 0 ? sfx::kFxNone : static_cast<uint16_t>(it->second);
        std::vector<uint8_t> blob;
        EffectStats st;
        int id = -1;
        if (bakeEffect(path, bank, o, blob, st)) {
            id = nextEffect++;
            items.push_back({"e_" + std::to_string(id), 3, std::move(blob)});
        } else {
            ++failures[st.why];
        }
        stats.push_back(st);
        effectOf.emplace(key, id);
        return id < 0 ? sfx::kFxNone : static_cast<uint16_t>(id);
    };

    std::unordered_map<uint32_t, uint32_t> svRow;
    for (uint32_t i = 0; i < sv.getRecordCount(); ++i) svRow[sv.getUInt32(i, 0)] = i;
    std::vector<sfx::SpellFxVisual> rows;
    size_t roles[3] = {0, 0, 0};
    for (uint32_t v : visuals) {
        auto r = svRow.find(v);
        if (r == svRow.end()) continue;
        const uint32_t i = r->second;
        const uint32_t fc = sv.getFieldCount();
        std::string pre = kSvPrecast < fc ? kitPath(sv.getUInt32(i, kSvPrecast)) : std::string{};
        std::string cast = kSvCast < fc ? kitPath(sv.getUInt32(i, kSvCast)) : std::string{};
        if (cast.empty() && kSvMissile < fc) cast = missilePath(sv.getUInt32(i, kSvMissile));
        std::string imp = kSvImpact < fc ? kitPath(sv.getUInt32(i, kSvImpact)) : std::string{};
        if (imp.empty() && kSvMissile < fc) imp = missilePath(sv.getUInt32(i, kSvMissile));
        sfx::SpellFxVisual row{};
        row.visual = v;
        row.precast = effectFor(pre);
        row.cast = effectFor(cast);
        row.impact = effectFor(imp);
        if (row.precast == sfx::kFxNone && row.cast == sfx::kFxNone && row.impact == sfx::kFxNone) continue;
        roles[0] += row.precast != sfx::kFxNone;
        roles[1] += row.cast != sfx::kFxNone;
        roles[2] += row.impact != sfx::kFxNone;
        rows.push_back(row);
    }

    std::vector<uint8_t> vis;
    sfx::SpellFxVisualTable vt{};
    std::memcpy(vt.magic, "WSFV", 4);
    vt.version = sfx::kSpellFxVersion;
    vt.count = static_cast<uint32_t>(rows.size());
    vt.effectCount = static_cast<uint32_t>(nextEffect);
    vt.textureCount = static_cast<uint32_t>(bank.count());
    vis.resize(sizeof(vt));
    std::memcpy(vis.data(), &vt, sizeof(vt));
    vis.insert(vis.end(), reinterpret_cast<const uint8_t*>(rows.data()),
               reinterpret_cast<const uint8_t*>(rows.data() + rows.size()));
    items.insert(items.begin(), PakItem{"vis", 3, std::move(vis)});

    size_t effectBytes = 0, maxBytes = 0, verts = 0, tris = 0, emit = 0, srcEmit = 0, dominant = 0, ribbons = 0;
    float worstKey = 0.0f;
    std::string worstKeyPath;
    std::vector<size_t> sizes;
    for (const auto& s : stats) {
        if (o.list)
            std::printf("%-58s %s attach=%2u dur=%5u kb=%5.1f verts=%4zu tris=%4zu groups=%2zu tracks=%2zu "
                        "emit=%zu/%zu tex=%zu batches=%zu ribbons=%zu err=%.3f mtx=%zu%s%s\n",
                        s.path.c_str(), s.ok ? "ok  " : "FAIL", s.attach, s.durationMs, s.bytes / 1024.0, s.vertices,
                        s.triangles, s.groups, s.tracks, s.emitters, s.sourceEmitters, s.textures, s.sourceBatches,
                        s.ribbons, s.keyError, s.matrixTracks, s.dominant ? " particle-dominant" : "",
                        s.ok ? "" : (" (" + s.why + ")").c_str());
        if (!s.ok) continue;
        effectBytes += s.bytes;
        maxBytes = std::max(maxBytes, s.bytes);
        sizes.push_back(s.bytes);
        verts += s.vertices;
        tris += s.triangles;
        emit += s.emitters;
        srcEmit += s.sourceEmitters;
        dominant += s.dominant;
        ribbons += s.ribbons ? 1 : 0;
        if (s.keyError > worstKey) {
            worstKey = s.keyError;
            worstKeyPath = s.path;
        }
    }
    std::sort(sizes.begin(), sizes.end());
    std::printf("spells: %zu (%s), visuals with a spell: %zu, baked rows: %zu (precast %zu, cast %zu, impact %zu)\n",
                spells.size(), o.all ? "all" : "class lines + hearthstone", visuals.size(), rows.size(), roles[0],
                roles[1], roles[2]);
    std::printf("effects: %d baked of %zu paths, %zu KB (median %.1f KB, largest %.1f KB), vertices %zu, triangles %zu, "
                "emitters %zu of %zu, particle-dominant %zu, with ribbons %zu\n",
                nextEffect, stats.size(), effectBytes / 1024, sizes.empty() ? 0.0 : sizes[sizes.size() / 2] / 1024.0,
                maxBytes / 1024.0, verts, tris, emit, srcEmit, dominant, ribbons);
    std::printf("track keys: largest error %.3f yd over the mesh bounds (%s)\n", worstKey, worstKeyPath.c_str());
    for (const auto& [why, n] : failures) std::printf("  not baked: %d %s\n", n, why.c_str());
    std::printf("textures: %zu, %zu KB (max %d px)\n", bank.count(), bank.bytes() / 1024, o.tex);
    if (!o.listMissing.empty()) {
        FILE* f = std::fopen(o.listMissing.c_str(), "wb");
        for (const auto& p : gMissing)
            if (f) std::fprintf(f, "%s\n", p.c_str());
        if (f) std::fclose(f);
        std::printf("%zu missing files listed in %s\n", gMissing.size(), o.listMissing.c_str());
    }
    if (!writePak(o.out, items)) return 1;
    return 0;
}
