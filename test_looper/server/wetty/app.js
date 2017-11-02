var express = require('express');
var url = require('url');
var http = require('http');
var https = require('https');
var path = require('path');
var server = require('socket.io');
var pty = require('pty.js');
var fs = require('fs');

var opts = require('optimist')
    .options({
        config: {
            demand: true,
            alias: "c",
            description: 'path to the config.json'
            },
        port: {
            demand: true,
            alias: "p",
            description: 'port to listen on'
            },
        }).boolean('allow_discovery').argv;

var port = opts.port

var runhttps = false;

if (opts.sslkey && opts.sslcert) {
    runhttps = true;
    opts['ssl'] = {};
    opts.ssl['key'] = fs.readFileSync(path.resolve(opts.sslkey));
    opts.ssl['cert'] = fs.readFileSync(path.resolve(opts.sslcert));
}

process.on('uncaughtException', function(e) {
    console.error('Error: ' + e);
});

var httpserv;

var app = express();
app.get('/wetty', function(req, res) {
    res.sendfile(__dirname + '/public/wetty/index.html');
});
app.use('/', express.static(path.join(__dirname, 'public')));

if (runhttps) {
    httpserv = https.createServer(opts.ssl, app).listen(port, function() {
        console.log('https on port ' + port);
    });
} else {
    httpserv = http.createServer(app).listen(port, function() {
        console.log('http on port ' + port);
    });
}

var io = server(httpserv,{path: '/wetty/socket.io'});
io.on('connection', function(socket){
    var request = socket.request;
    console.log((new Date()) + ' Connection accepted.');

    var query = url.parse(request.headers.referer, true).query;

    var commit = query.commit
    var test = query.test
    var ports = query.ports

    if (!(commit != null && test != null)) {
        socket.emit('output', "INVALID URL: " + request.headers.referer)
        socket.disconnect();
        return
    }

    var term;
    var args = ["invoke.py", opts.config, commit, test]

    if (ports != null) {
        args.push('--ports')
        args.push(ports)
    }

    console.log("Invoking invoke.py with args " + JSON.stringify(args, null, 2))

    term = pty.spawn('/usr/bin/python', args, {
        name: 'xterm-256color',
        cols: 80,
        rows: 30
    })

    term.on('data', function(data) {
        socket.emit('output', data);
    });
    term.on('exit', function(code) {
        console.log((new Date()) + " PID=" + term.pid + " ENDED")
    });
    socket.on('resize', function(data) {
        term.resize(data.col, data.row);
    });
    socket.on('input', function(data) {
        term.write(data);
    });
    socket.on('disconnect', function() {
        console.log("SHUTTING DOWN terminal")
        term.kill("SIGTERM");
    });
})
