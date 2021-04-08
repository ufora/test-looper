#!/usr/bin/python3

import cherrypy
import sys


class Root(object):
    @cherrypy.expose
    def index(self):
        raise cherrypy.HTTPRedirect("https://" + sys.argv[1] + ":443/")


if __name__ == "__main__":
    config = {
        "global": {
            "engine.autoreload.on": False,
            "server.socket_host": "0.0.0.0",
            "server.socket_port": 80,
            "server.show_tracebacks": False,
            "request.show_tracebacks": False,
            "tools.sessions.on": True,
        }
    }
    cherrypy.config.update(config)
    cherrypy.quickstart(Root(), "/")
