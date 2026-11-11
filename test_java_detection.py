from pathlib import Path
from agent.java_test_harness import detect_java_project, JavaProjectError

TEST_PATHS = [
    Path("/Users/ianayotte/CMPT Files/Test Repos/console-tools-master"),
    Path("/Users/ianayotte/CMPT Files/Test Repos/console-tools-master/src"),
]

for path in TEST_PATHS:
    print("\n" + "=" * 60)
    print(f"Testing: {path}")

    try:
        project = detect_java_project(path)

        print(f"Root:          {project.root}")
        print(f"Build system:  {project.build_system}")
        print(f"Build command: {project.build_command}")
        print(f"Build file:    {project.build_file}")

    except JavaProjectError as error:
        print(f"Detection error: {error}")