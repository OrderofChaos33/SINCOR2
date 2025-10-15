from flask import Flask, render_template_string

app = Flask(__name__)

@app.route('/')
def home():
    return render_template_string('''
<!DOCTYPE html>
<html>
<head>
    <title>SINCOR Test</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 40px; }
        button { padding: 10px 20px; margin: 5px; cursor: pointer; }
        .working { background: green; color: white; }
        .broken { background: red; color: white; }
        #result { margin: 20px 0; padding: 20px; border: 1px solid #ccc; }
    </style>
</head>
<body>
    <h1>SINCOR Simple Test</h1>
    <p>This is a basic test to see if Flask is working</p>
    
    <button onclick="testFunction()" class="working">Test JavaScript</button>
    <button onclick="fetch('/api/test').then(r=>r.json()).then(d=>document.getElementById('result').innerHTML=JSON.stringify(d))" class="working">Test API</button>
    
    <div id="result">Results will appear here...</div>
    
    <script>
        function testFunction() {
            document.getElementById('result').innerHTML = 'JavaScript is working!';
        }
    </script>
</body>
</html>
    ''')

@app.route('/api/test')
def test_api():
    return {'status': 'working', 'message': 'API is functional'}

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5001, debug=True)