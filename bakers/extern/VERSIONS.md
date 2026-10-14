# Vendored Library Versions

Versions of third-party libraries vendored in `extern/`. Update this file
when upgrading any dependency so maintainers can track drift.

| Library | Version | Source | Notes |
|---------|---------|--------|-------|
| Dear ImGui | 1.92.6 WIP | https://github.com/ocornut/imgui | Git submodule |
| vk-bootstrap | v1.3.302 | https://github.com/charles-lunarg/vk-bootstrap | Git submodule |
| FidelityFX-FSR2 | v2.2.1-1-g3d22aef | https://github.com/GPUOpen-Effects/FidelityFX-FSR2 | Git submodule |
| FidelityFX-SDK | tracks `main` (ce81c67…) | https://github.com/Kelsidavis/FidelityFX-SDK | Git submodule (fork pinned by build scripts) |
| Vulkan Memory Allocator | 3.4.0 | https://github.com/GPUOpen-LibrariesAndSDKs/VulkanMemoryAllocator | Single header |
| miniaudio | 0.11.24 | https://miniaud.io/ | Single header |
| nlohmann/json | 3.11.3 | https://github.com/nlohmann/json | Single header (`extern/nlohmann/json.hpp`) |
| stb_image | 2.30 | https://github.com/nothings/stb | Single header |
| stb_image_write | 1.16 | https://github.com/nothings/stb | Single header |
| Catch2 | amalgamated (Catch2 v3.x) | https://github.com/catchorg/Catch2 | Vendored `catch_amalgamated.{cpp,hpp}` — test build only |
| Lua | 5.1.5 | https://www.lua.org/ | Intentionally 5.1 for WoW addon API compatibility |
