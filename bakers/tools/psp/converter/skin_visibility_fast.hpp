// PSP: host-only whole-triangle proof; caller must qualify the renderer envelope.
#pragma once
#include "skin_visibility.hpp"

namespace wowee::pspconv::skin_visibility {
struct TriangleGroups {
    std::array<size_t,3> indices{};
    uint8_t count=0;
};
struct PreparedTopology {
    bool valid=false;
    size_t vertexCount=0;
    std::vector<IndexTriangle> triangles;
    std::vector<uint32_t> originalM2Ids, sourceIds;
    std::vector<size_t> instanceGroupIndices;
    std::vector<TriangleGroups> triangleGroups;
};
struct VertexMasks { uint8_t source=0,candidate=0;bool boundary=false; };
struct PoseWorkspace {
    MeshProof proof;
    std::vector<VertexMasks> masks;
};
inline PreparedTopology prepareTopology(size_t vertexCount,const std::vector<IndexTriangle>& triangles,
                                        const std::vector<uint32_t>& originalM2Ids) {
    PreparedTopology topology;topology.vertexCount=vertexCount;
    topology.triangles=triangles;topology.originalM2Ids=originalM2Ids;
    if(originalM2Ids.size()!=vertexCount)return topology;
    for(const auto& triangle:triangles)for(size_t k=0;k<3;++k)
        if(triangle[k]>=vertexCount)return topology;
    topology.sourceIds=originalM2Ids;
    std::sort(topology.sourceIds.begin(),topology.sourceIds.end());
    topology.sourceIds.erase(std::unique(topology.sourceIds.begin(),topology.sourceIds.end()),topology.sourceIds.end());
    topology.instanceGroupIndices.reserve(vertexCount);
    for(uint32_t id:originalM2Ids)
        topology.instanceGroupIndices.push_back(static_cast<size_t>(
            std::lower_bound(topology.sourceIds.begin(),topology.sourceIds.end(),id)-topology.sourceIds.begin()));
    topology.triangleGroups.resize(triangles.size());
    for(size_t t=0;t<triangles.size();++t) {
        std::array<size_t,3> groups{{topology.instanceGroupIndices[triangles[t][0]],
                                   topology.instanceGroupIndices[triangles[t][1]],
                                   topology.instanceGroupIndices[triangles[t][2]]}};
        std::sort(groups.begin(),groups.end());
        auto& target=topology.triangleGroups[t];
        for(size_t k=0;k<groups.size();++k)
            if(!k||groups[k]!=groups[k-1])target.indices[target.count++]=groups[k];
    }
    topology.valid=true;
    return topology;
}
// Topology is immutable/shareable. Each concurrent caller owns its workspace;
// the returned proof is borrowed until that workspace's next pose call.
inline const MeshProof& classifyMeshFast(const std::vector<Point3>& source,const std::vector<Point3>& candidate,
                                        const PreparedTopology& topology,double envelope,PoseWorkspace& workspace) {
    auto& proof=workspace.proof;
    proof.inputValid=false;
    proof.canSkipInstance.assign(source.size(),0);
    proof.triangleProofs.assign(topology.triangles.size(),TriangleProof{});
    proof.sourceIds.clear();
    proof.outsideTriangles=0;proof.degenerateTriangles=0;proof.unprovedTriangles=topology.triangles.size();
    if(!topology.valid||source.size()!=topology.vertexCount||candidate.size()!=source.size()||
       !std::isfinite(envelope)||envelope<=0)return proof;
    workspace.masks.resize(source.size());
    for(size_t i=0;i<source.size();++i) {
        const auto& s=source[i];const auto& c=candidate[i];
        if(!finitePoint(s)||!finitePoint(c))return proof;
        VertexMasks mask{};
        for(size_t axis=0;axis<3;++axis) {
            if(s[axis]>envelope)mask.source|=static_cast<uint8_t>(1u<<(axis*2));
            if(s[axis]<-envelope)mask.source|=static_cast<uint8_t>(1u<<(axis*2+1));
            if(c[axis]>envelope)mask.candidate|=static_cast<uint8_t>(1u<<(axis*2));
            if(c[axis]<-envelope)mask.candidate|=static_cast<uint8_t>(1u<<(axis*2+1));
            mask.boundary|=s[axis]==envelope||s[axis]==-envelope||c[axis]==envelope||c[axis]==-envelope;
        }
        workspace.masks[i]=mask;
    }
    proof.inputValid=true;proof.unprovedTriangles=0;
    proof.sourceIds.assign(topology.sourceIds.size(),SourceIdProof{});
    for(size_t i=0;i<topology.sourceIds.size();++i)proof.sourceIds[i].sourceId=topology.sourceIds[i];
    for(size_t t=0;t<topology.triangles.size();++t) {
        const auto& triangle=topology.triangles[t];
        const auto& a=workspace.masks[triangle[0]];
        const auto& b=workspace.masks[triangle[1]];
        const auto& c=workspace.masks[triangle[2]];
        TriangleProof result;
        if(!a.boundary&&!b.boundary&&!c.boundary) {
            const uint8_t sourceMask=static_cast<uint8_t>(a.source&b.source&c.source);
            const uint8_t candidateMask=static_cast<uint8_t>(a.candidate&b.candidate&c.candidate);
            const uint8_t common=static_cast<uint8_t>(sourceMask&candidateMask);
            if(common) {
                uint8_t bit=0;while(!(common&(1u<<bit)))++bit;
                const uint8_t axis=bit/2;
                const int8_t sign=(bit&1u)?-1:1;
                double extreme=sign>0?std::numeric_limits<double>::infinity():-std::numeric_limits<double>::infinity();
                for(const auto* positions:{&source,&candidate})for(size_t k=0;k<3;++k) {
                    const double value=(*positions)[triangle[k]][axis];
                    extreme=sign>0?std::min(extreme,value):std::max(extreme,value);
                }
                result={TriangleKind::Outside,axis,sign,sign>0?extreme-envelope:-envelope-extreme};
            } else if(!sourceMask&&!candidateMask) {
                for(size_t i=0;i<3&&result.kind!=TriangleKind::Degenerate;++i)
                    for(size_t j=i+1;j<3;++j)
                        if(source[triangle[i]]==source[triangle[j]]&&candidate[triangle[i]]==candidate[triangle[j]])
                            result={TriangleKind::Degenerate,0,0,0};
            }
        }
        proof.triangleProofs[t]=result;
        if(result.kind==TriangleKind::Outside)++proof.outsideTriangles;
        else if(result.kind==TriangleKind::Degenerate) {++proof.degenerateTriangles;continue;}
        else ++proof.unprovedTriangles;
        const auto& groups=topology.triangleGroups[t];
        for(uint8_t k=0;k<groups.count;++k) {
            auto& group=proof.sourceIds[groups.indices[k]];
            ++group.consideredIncidents;
            if(result.kind!=TriangleKind::Outside)++group.unprovedIncidents;
        }
    }
    for(auto& group:proof.sourceIds)group.canSkip=group.consideredIncidents>0&&group.unprovedIncidents==0;
    for(size_t i=0;i<source.size();++i)
        proof.canSkipInstance[i]=proof.sourceIds[topology.instanceGroupIndices[i]].canSkip?1:0;
    return proof;
}
} // namespace wowee::pspconv::skin_visibility
