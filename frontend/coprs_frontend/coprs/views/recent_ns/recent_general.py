import flask, os

from urlparse import urlparse
from . import recent_ns
from coprs.logic import builds_logic
from coprs import helpers

@recent_ns.route("/")
def recent():
    # tasks = bilds_logic.BuildsLogic.get_build_tasks(
    builds = builds_logic.BuildsLogic.get_recent_tasks(limit=20)
    return flask.render_template("recent.html",
                            number=len(list(builds)),
                            builds=builds)