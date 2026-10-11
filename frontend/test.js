const WebSocket = require('ws');

console.log("Connecting...");
const ws = new WebSocket('wss://migiarbitrage-api-oNpGL.azurewebsites.net');

ws.on('open', function open() {
  console.log('Connected!');
});

ws.on('message', function incoming(data) {
  console.log('Received:', data.toString().substring(0, 300));
});

ws.on('error', function error(err) {
  console.log('Error:', err);
});

ws.on('close', function close() {
  console.log('Disconnected');
});
