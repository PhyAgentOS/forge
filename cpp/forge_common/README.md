# forge_common C++

Lightweight C++ shared utilities for Forge packages.

The C++ package currently provides standard-library-only logging helpers. It
does not depend on `spdlog` or other third-party logging libraries.

## Capability status

Publish/receive observability is not implemented in the C++ library yet. C++
applications must not assume that the Python or Rust helper APIs exist here or
advertise a C++ boundary as instrumented merely because metadata is forwarded.
The future implementation must follow the same
[language-neutral metadata protocol](../../interfaces/observability/PROTOCOL.md).
Current language support and runnable examples are listed in the
[observability documentation index](../../interfaces/observability/README.md).

## Usage

```cpp
#include <forge_common/forge_common.hpp>

int main() {
  forge_common::ConfigureFromEnv();
  auto logger = forge_common::GetLogger("my-node");
  logger.Info("Forge component started");
}
```

For Rust-style one-time initialization:

```cpp
forge_common::InitLogging("my-node");
```

## Environment variables

- `FORGE_LOG_LEVEL`: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`, or `OFF`
- `FORGE_LOG_FILE`: optional file path for log output
- `FORGE_LOG_CONSOLE`: whether console logging is enabled (`true` or `false`)
- `FORGE_LOG_STREAM`: console stream (`stdout` or `stderr`)

## CMake

```cmake
add_subdirectory(path/to/forge/cpp/forge_common)
target_link_libraries(my_app PRIVATE forge_common::forge_common)
```

Or use `FetchContent` with the Forge git repository and
`SOURCE_SUBDIR cpp/forge_common`.
