import asyncio
from functools import wraps


def log_call(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        return function(*args, **kwargs)
    return wrapper


@log_call
async def fetch_grade(student_id):
    await asyncio.sleep(0)
    return {"student_id": student_id, "grade": 95}