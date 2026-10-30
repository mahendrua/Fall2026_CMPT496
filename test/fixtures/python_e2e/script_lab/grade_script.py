"""Script-only grade calculator for offline Checkpoint integration tests.

Stdin/stdout protocol (do not import this module):
1. Read one line: an integer score.
2. Print PASS if the score is at least 50, otherwise FAIL.
3. Exit.

Example:
    echo 72 | python grade_script.py
    -> PASS
"""
score = int(input())
if score >= 50:
    print("PASS")
else:
    print("FAIL")
