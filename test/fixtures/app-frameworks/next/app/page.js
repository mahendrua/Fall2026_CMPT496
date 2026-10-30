'use client';
import { useState } from 'react';
export default function Page() { const [count,setCount]=useState(0); return <><h1>Checkpoint Next Fixture</h1><button onClick={()=>{setCount(count+1);document.body.style.background=(count+1)%2?'rgb(219,44,153)':'rgb(21,196,119)';}}>Increase</button><output>{count}</output></>; }
