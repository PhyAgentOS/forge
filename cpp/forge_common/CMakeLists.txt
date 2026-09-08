cmake_minimum_required(VERSION 3.20)

project(forge_common_cpp VERSION 2.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

option(FORGE_COMMON_CPP_BUILD_TESTS "Build C++ forge_common tests" ${PROJECT_IS_TOP_LEVEL})

add_library(forge_common_cpp
  src/logger.cpp
)
add_library(forge_common::forge_common ALIAS forge_common_cpp)

target_include_directories(forge_common_cpp
  PUBLIC
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
    $<INSTALL_INTERFACE:include>
)

if(FORGE_COMMON_CPP_BUILD_TESTS)
  enable_testing()

  add_executable(forge_common_cpp_tests tests/logger_tests.cpp)
  target_link_libraries(forge_common_cpp_tests PRIVATE forge_common_cpp)
  add_test(NAME forge_common_cpp_tests COMMAND forge_common_cpp_tests)

  set(FORGE_COMMON_CPP_CONSUMER_BUILD_DIR ${CMAKE_CURRENT_BINARY_DIR}/fetch_content_consumer)
  add_test(
    NAME forge_common_fetch_content_consumer_configure
    COMMAND ${CMAKE_COMMAND}
      -S ${CMAKE_CURRENT_SOURCE_DIR}/tests/fetch_content_consumer
      -B ${FORGE_COMMON_CPP_CONSUMER_BUILD_DIR}
      -G ${CMAKE_GENERATOR}
      -DFORGE_FETCH_SOURCE_DIR=${CMAKE_CURRENT_SOURCE_DIR}/../..
  )
  add_test(
    NAME forge_common_fetch_content_consumer_build
    COMMAND ${CMAKE_COMMAND} --build ${FORGE_COMMON_CPP_CONSUMER_BUILD_DIR}
  )
  set_tests_properties(
    forge_common_fetch_content_consumer_build
    PROPERTIES DEPENDS forge_common_fetch_content_consumer_configure
  )
endif()
