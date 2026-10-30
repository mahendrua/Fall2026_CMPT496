const app = require('express')();
app.get('/', (_req,res) => res.send(`<!doctype html><body style="background:rgb(21,196,119)"><h1>Checkpoint Express Fixture</h1><button onclick="document.querySelector('output').textContent++;document.body.style.background=Number(document.querySelector('output').textContent)%2?'rgb(219,44,153)':'rgb(21,196,119)'">Increase</button><output>0</output>`));
app.listen(Number(process.env.PORT), '0.0.0.0', () => console.log(`http://localhost:${process.env.PORT}`));
