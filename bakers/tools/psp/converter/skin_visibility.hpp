// PSP: host-only whole-triangle proof; caller must qualify the renderer envelope.
#pragma once
#include <algorithm>
#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <map>
#include <vector>

namespace wowee::pspconv::skin_visibility {
struct Point3 {
    double xyz[3]{};
    double& operator[](size_t i) { return xyz[i]; }
    double operator[](size_t i) const { return xyz[i]; }
};
inline bool operator==(const Point3& a,const Point3& b) {
    return a[0]==b[0]&&a[1]==b[1]&&a[2]==b[2];
}
using Triangle=std::array<Point3,3>;
struct IndexTriangle {
    size_t indices[3]{};
    size_t operator[](size_t i) const { return indices[i]; }
};
enum class TriangleKind { Unproved, Outside, Degenerate };
struct TriangleProof {
    TriangleKind kind=TriangleKind::Unproved;
    uint8_t axis=0;
    int8_t sign=0;
    double minimumMargin=0;
};
struct SourceIdProof {
    uint32_t sourceId=0;
    size_t consideredIncidents=0, unprovedIncidents=0;
    bool canSkip=false;
};
struct MeshProof {
    bool inputValid=false;
    std::vector<uint8_t> canSkipInstance;
    std::vector<TriangleProof> triangleProofs;
    std::vector<SourceIdProof> sourceIds;
    size_t outsideTriangles=0, degenerateTriangles=0, unprovedTriangles=0;
};
inline bool finitePoint(const Point3& p) {
    return std::isfinite(p[0])&&std::isfinite(p[1])&&std::isfinite(p[2]);
}
inline TriangleProof classifyTriangle(const Triangle& source,const Triangle& candidate,double envelope) {
    if(!std::isfinite(envelope)||envelope<=0)return {};
    for(const auto* triangle:{&source,&candidate})
        for(const auto& point:*triangle) {
            if(!finitePoint(point))return {};
            // Deliberately conservative: exact boundary contact never waives.
            for(size_t axis=0;axis<3;++axis)
                if(point[axis]==envelope||point[axis]==-envelope)return {};
        }
    // A common halfspace is a convex set: all three corners imply the entire
    // triangle is outside. Requiring the SAME plane for all six corners also
    // prevents a source-only or opposite-side candidate waiver.
    for(uint8_t axis=0;axis<3;++axis) {
        double lo=std::numeric_limits<double>::infinity();
        double hi=-std::numeric_limits<double>::infinity();
        for(const auto* triangle:{&source,&candidate})for(const auto& point:*triangle) {
            lo=std::min(lo,point[axis]);hi=std::max(hi,point[axis]);
        }
        if(lo>envelope)return {TriangleKind::Outside,axis,1,lo-envelope};
        if(hi<-envelope)return {TriangleKind::Outside,axis,-1,-envelope-hi};
    }
    // If one whole pose is far but no common plane was proved, degeneracy
    // must not conceal a far-to-near or opposite-halfspace mismatch.
    for(const auto* triangle:{&source,&candidate})for(size_t axis=0;axis<3;++axis) {
        bool positive=true,negative=true;
        for(const auto& point:*triangle) {
            positive&=point[axis]>envelope;negative&=point[axis]<-envelope;
        }
        if(positive||negative)return {};
    }
    // Test outside FIRST: far float32 geometry may collapse to a point, yet it
    // still has a useful positive outside proof. Near bridge exclusion needs
    // the identical equal corner pair in both poses, without an epsilon.
    for(size_t a=0;a<3;++a)for(size_t b=a+1;b<3;++b)
        if(source[a]==source[b]&&candidate[a]==candidate[b])
            return {TriangleKind::Degenerate,0,0,0};
    return {};
}
inline MeshProof classifyMesh(const std::vector<Point3>& source,const std::vector<Point3>& candidate,
                              const std::vector<IndexTriangle>& triangles,
                              const std::vector<uint32_t>& originalM2Ids,double envelope) {
    MeshProof proof;
    proof.canSkipInstance.resize(source.size());
    proof.triangleProofs.resize(triangles.size());
    proof.unprovedTriangles=triangles.size();
    if(source.size()!=candidate.size()||source.size()!=originalM2Ids.size()||
       !std::isfinite(envelope)||envelope<=0)return proof;
    for(size_t i=0;i<source.size();++i)
        if(!finitePoint(source[i])||!finitePoint(candidate[i]))return proof;
    for(const auto& triangle:triangles)for(size_t k=0;k<3;++k)
        if(triangle[k]>=source.size())return proof;
    proof.inputValid=true;
    proof.unprovedTriangles=0;
    std::map<uint32_t,SourceIdProof> groups;
    for(uint32_t id:originalM2Ids)groups.emplace(id,SourceIdProof{id,0,0,false});
    for(size_t t=0;t<triangles.size();++t) {
        const auto& indices=triangles[t];
        const Triangle s{{source[indices[0]],source[indices[1]],source[indices[2]]}};
        const Triangle c{{candidate[indices[0]],candidate[indices[1]],candidate[indices[2]]}};
        const auto result=classifyTriangle(s,c,envelope);
        proof.triangleProofs[t]=result;
        if(result.kind==TriangleKind::Outside)++proof.outsideTriangles;
        else if(result.kind==TriangleKind::Degenerate) {++proof.degenerateTriangles;continue;}
        else ++proof.unprovedTriangles;
        // Geometry may have duplicated emitted instances of one M2 vertex.
        // Every incident triangle of EVERY instance constrains that source ID.
        std::array<uint32_t,3> ids{{originalM2Ids[indices[0]],originalM2Ids[indices[1]],originalM2Ids[indices[2]]}};
        std::sort(ids.begin(),ids.end());
        for(size_t k=0;k<ids.size();++k) {
            if(k&&ids[k]==ids[k-1])continue;
            auto& group=groups.at(ids[k]);
            ++group.consideredIncidents;
            if(result.kind!=TriangleKind::Outside)++group.unprovedIncidents;
        }
    }
    for(auto& item:groups) {
        auto& group=item.second;
        // No vacuous proof for unused vertices or only-near-degenerate bridges.
        group.canSkip=group.consideredIncidents>0&&group.unprovedIncidents==0;
        proof.sourceIds.push_back(group);
    }
    for(size_t i=0;i<originalM2Ids.size();++i)
        proof.canSkipInstance[i]=groups.at(originalM2Ids[i]).canSkip?1:0;
    return proof;
}
} // namespace wowee::pspconv::skin_visibility
