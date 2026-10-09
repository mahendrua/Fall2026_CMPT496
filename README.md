# Checkpoint

## Purpose
This repository contains a tool which can be used to perform analysis and test generation for large codebases. The tool creates as output:
- Summaries of each file
- Code structure information for each file, including PlantUML
- Summaries of the contents of each directory
- A summary of the entire repository
- A list of business rules extracted from the codebase
- UML reports for each given file
- Unit tests for the codebase
- A test report for all generated unit tests

This is all completed using LangGraph, with an LLM acting as the generator of outputs.

## Installation
- If you are a user then navigate to the releases tab of this repository and follow the instructions under the latest release
- If you are a dev then continue reading to setup the dev environment on your device

## Notes
- This tool is currently able to parse codebases written in C# or Javascript for the vector store component. For the unit test component only C# is supported. Other languages are not supported
- The system defaults to using Gemini 3 Flash, given a valid API key in a .env file. This file is generated automatically
- The system is mostly set up to support the use of other models. Additional steps required: Either the agents should be instantiated and run manually, by passing in the desired model, or main.py should be modified slightly to do so
- When running the tool, a filepath to the target codebase is needed. Use absolute paths
- It is recommended to store the target codebase in a directory in the root of the project called "targetCodebases", as this directory is already included in the .gitignore. The target codebase can be located anywhere though

## Requirements

**Python 3.13+** is required for this project.
**Java 11+** is required for this project.
**DotNet 10+** is required for this project.

Download Python: [https://www.python.org/downloads/](https://www.python.org/downloads/)
Downlaod Java: 
Download Dotnet: 

## Setup Instructions

1. **Clone the repository**

2. **Navigate to the directory containing the project folder:**
   ```powershell
   cd path/to/project
   ```

3. **Create a virtual environment:**
   ```powershell
   python -m venv .venv
   ```

4. **Activate the virtual environment:**
   ```powershell
   .\.venv\Scripts\Activate.ps1
   ```

5. **Install Python dependencies:**
   ```powershell
   pip install -r requirements.txt
   ```

6. **Install Node.js dependencies:**
   ```powershell
   npm install
   ```

7. **Run the program:**
   ```powershell
   npm.cmd start
   ```

## Deactivate Virtual Environment
```powershell
deactivate
```

## Convert JSON summaries to Markdown

```powershell
# default: reads `agent/file_summary_agent_output` and writes to `<input-dir>/markdown`
python -m utils.json_to_markdown

# print to stdout for quick verification
python -m utils.json_to_markdown --stdout

# specify input/output and avoid overwriting
python -m utils.json_to_markdown --input-dir agent/file_summary_agent_output --output-dir ./markdown --no-overwrite
```

Flags:
- `--input-dir`: directory containing JSON summary files (default: `agent/file_summary_agent_output`)
- `--output-dir`: directory to write markdown files (default: `<input-dir>/markdown`)
- `--stdout`: print results instead of writing files
- `--overwrite` / `--no-overwrite`: controls replacing existing `.md` files (default: overwrite enabled)
- `--ext`: file extension to search for (default: `.json`)

Notes:
- Run the command from the project root so the default input path resolves correctly.

## Manual GUI testing

The **GUI Test Cases** screen runs uploaded JavaScript and TypeScript Playwright tests in a separate Docker environment. Docker Desktop must be installed, running Linux containers, and have at least 2 GB of available memory. The Playwright and proxy images are built automatically on the first run and rebuilt when their bundled Docker contexts change; building requires an internet connection.

Upload up to 20 `.js` or `.ts` files (2 MB each, 20 MB total). Select test files to run; unselected uploads remain available as relative helper modules. Tests should use `@playwright/test` or `playwright/test`; additional npm packages are not installed.

Before running, enter each allowed website hostname on its own line. Hostnames allow HTTP and HTTPS on ports 80 and 443; adding a port also allows that port, for example `app.example.com:5173`. Host entries match exactly; prefix with `*.` to allow the hostname and its subdomains. Private network destinations are blocked. To test a web app running on the host machine, use `host.docker.internal:<port>` instead of `localhost` and explicitly allow it.

The application mounts only copied test files into the runner, read-only, and runs the browser in a resource-limited container. Browser traffic is routed through a separate allowlisting proxy; the live browser preview and status stream are relayed through loopback-only host ports, while the runner remains isolated from the host network. Uploaded tests are executable code: only run files you trust.

## Packaging the backend using Pyinstaller

Package backend using main.spec found in releases. Copy and paste any needed programs in before going to the next step. Stuff like plantuml.jar

Use this command to package the backend assuming the project is located directly in your c drive. Otherwise use the correct path to your project
```powershell
pyinstaller --clean --distpath "C:\CMPT-496-Capstone\releases" --workpath "C:\CMPT-496-Capstone\build" "releases\main.spec"
```

## Packaging the electron application

```powershell
npm run dist
```
