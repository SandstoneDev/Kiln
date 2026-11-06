// PSP: bind an exactly rebaked NPC skin to its existing texture/attachment IDs.
// Input/output are raw n_ blobs in a separate staging directory, not game packs.
#include "skin_fresh_import.hpp"
#include <cstdio>
#include <fstream>

static std::vector<uint8_t> load(const char* path) {
    std::ifstream file(path,std::ios::binary);
    if(!file)throw std::runtime_error("cannot open input skin");
    file.seekg(0,std::ios::end);const auto size=file.tellg();
    if(size<static_cast<std::streamoff>(sizeof(wowee::rendering::psp::SkinHeader)) || size>64*1024*1024)
        throw std::runtime_error("input skin size outside supported bounds");
    file.seekg(0);std::vector<uint8_t> data(static_cast<size_t>(size));
    file.read(reinterpret_cast<char*>(data.data()),static_cast<std::streamsize>(data.size()));
    if(!file)throw std::runtime_error("cannot read input skin");
    return data;
}

int main(int argc,char** argv) {
    if(argc!=4){std::fprintf(stderr,"usage: psp_import_skin OLD.bin FRESH.bin NEW.bin\n");return 2;}
    try {
        const auto prior=load(argv[1]),fresh=load(argv[2]);
        const auto data=wowee::pspconv::importFreshSkin(prior,fresh);
        // Exclusive creation: neither input nor an existing staged result is overwritten.
        FILE* file=std::fopen(argv[3],"wbx");
        if(!file)throw std::runtime_error("output cannot be created exclusively");
        bool okay=std::fwrite(data.data(),1,data.size(),file)==data.size();
        if(std::fclose(file))okay=false;
        if(!okay)throw std::runtime_error("cannot write imported skin");
        std::printf("imported %zu bytes; existing texture IDs and attachment bone indices retained\n",data.size());
        return 0;
    } catch(const std::exception& e){std::fprintf(stderr,"skin import: %s\n",e.what());return 3;}
}
