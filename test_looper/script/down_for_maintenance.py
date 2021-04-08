#!/usr/bin/python3

import cherrypy
import sys


class Root(object):
    @cherrypy.expose
    def default(self, *args, **kwargs):
        return "Testlooper is down for maintenance."


if __name__ == "__main__":
    config = {
        "global": {
            "engine.autoreload.on": False,
            "server.socket_host": "0.0.0.0",
            "server.socket_port": 443,
            "server.show_tracebacks": False,
            "request.show_tracebacks": False,
            "tools.sessions.on": True,
            "server.ssl_module": "builtin",
            "server.ssl_certificate": sys.argv[1],
            "server.ssl_private_key": sys.argv[2],
            "server.ssl_certificate_chain": sys.argv[3],
        }
    }
    cherrypy.config.update(config)
    cherrypy.quickstart(Root(), "/")
