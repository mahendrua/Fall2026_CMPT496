from flask import Flask
app = Flask(__name__)

@app.get('/')
def index():
    return '''<!doctype html><body style="background:rgb(21,196,119)"><h1>Checkpoint Flask Fixture</h1><button onclick="document.querySelector('output').textContent++;document.body.style.background=Number(document.querySelector('output').textContent)%2?'rgb(219,44,153)':'rgb(21,196,119)'">Increase</button><output>0</output>'''
