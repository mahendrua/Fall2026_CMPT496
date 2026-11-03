
"""
Test Tree-sitter query configuration, chunk extraction,
and import/namespace metadata across supported languages.
"""

from pathlib import Path
from tempfile import TemporaryDirectory

import tree_sitter_language_pack as tslp
from tree_sitter import Parser

from utils.tree_parse import LANGUAGE_DICT, parse_file, get_chunks


SAMPLES = {
    ".ts": """
import { readFile } from 'fs';

class Example {
    private value: number = 10;

    calculate(input: number): number {
        return input + this.value;
    }
}

function helper(): void {
    console.log("helper");
}
""",

    ".tsx": """
import React from 'react';

function Example() {
    return <div>Hello World</div>;
}

export default Example;
""",

    ".go": """
package main

import "fmt"

type Example struct {
    Value int
}

func (e *Example) Calculate(input int) int {
    return input + e.Value
}

func helper() {
    fmt.Println("helper")
}
""",

    ".rs": """
use std::fmt;

struct Example {
    value: i32,
}

impl Example {
    fn calculate(&self, input: i32) -> i32 {
        input + self.value
    }
}

fn helper() {
    println!("helper");
}
""",

    ".java": """
package example;

import java.util.List;

public class Example {
    private int value;

    public Example(int value) {
        this.value = value;
    }

    public int calculate(int input) {
        return input + value;
    }
}
""",

    ".c": """
#include <stdio.h>

struct Example {
    int value;
};

int calculate(int input) {
    return input + 10;
}

int main() {
    printf("Hello World");
    return 0;
}
""",

    ".cs": """
using System;

namespace Example;

public class ExampleClass {
    private int value;

    public int Value { get; set; }

    public ExampleClass(int value) {
        this.value = value;
    }

    public int Calculate(int input) {
        return input + value;
    }
}
""",

    ".js": """
import fs from 'fs';

class Example {
    constructor(value) {
        this.value = value;
    }

    calculate(input) {
        return input + this.value;
    }
}

function helper() {
    console.log("helper");
}
""",

    ".py": """
import os

class Example:
    def calculate(self, input):
        return input + 10

def helper():
    print("helper")

result = Example().calculate(5)
""",
}


EXPECTED_IMPORTS = {
    ".ts": "readFile",
    ".tsx": "React",
    ".go": "fmt",
    ".rs": "std::fmt",
    ".java": "java.util.List",
    ".c": "stdio.h",
    ".cs": "System",
    ".js": "fs",
    ".py": "import os",
}


def main():
    """Run parsing, extraction, and metadata tests for every configured language."""

    passed = 0
    failed = 0

    with TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        for extension, source in SAMPLES.items():
            config = LANGUAGE_DICT.get(extension)

            print("\n" + "=" * 55)
            print(f"TESTING {extension} ({config['name'] if config else 'unknown'})")
            print("=" * 55)

            if config is None:
                print(f"[FAIL] No LANGUAGE_DICT configuration for {extension}")
                failed += 1
                continue

            try:
                filename = temp_path / f"sample{extension}"
                filename.write_text(source, encoding="utf-8")

                language = tslp.get_language(config["name"])
                parser = Parser(language)

                bundle = parse_file(
                    filename,
                    parser,
                    config["name"],
                )

                chunks = get_chunks(bundle)

                print(f"Parsing: {filename}")
                print(f"Extracted {len(chunks)} chunks:")

                for chunk in chunks:
                    print(
                        f"  {chunk['type']}: "
                        f"{chunk['name']} "
                        f"[{chunk['start_line']}-{chunk['end_line']}] "
                        f"container={chunk['container']}"
                    )

                if not chunks:
                    print("[FAIL] No chunks extracted")
                    failed += 1
                    continue

                # Metadata is shared across chunks from the same source file.
                namespace = chunks[0]["namespace"]
                imports = chunks[0]["imports"]

                print(f"\n  Namespace: {namespace!r}")
                print(f"  Imports: {imports!r}")

                # Check that the expected import declaration was captured.
                expected_import = EXPECTED_IMPORTS[extension]
                imports_ok = expected_import in imports

                if imports_ok:
                    print(f"  [PASS] Import metadata contains '{expected_import}'")
                else:
                    print(f"  [FAIL] Expected import '{expected_import}' was not found")

                if imports_ok:
                    passed += 1
                else:
                    failed += 1

            except Exception as error:
                print(f"[FAIL] {type(error).__name__}: {error}")
                failed += 1

    print("\n" + "=" * 55)
    print("TEST SUMMARY")
    print("=" * 55)
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")
    print(f"Total:  {passed + failed}")


if __name__ == "__main__":
    main()
