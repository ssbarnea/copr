import flask

from coprs.views.status_ns import status_ns
from coprs.logic import builds_logic
from coprs import helpers


@status_ns.route("/")
@status_ns.route("/waiting/")
def waiting():
    tasks = builds_logic.BuildsLogic.get_build_task_queue(is_background=False).limit(200)
    bg_tasks_cnt = builds_logic.BuildsLogic.get_build_task_queue(is_background=True).count()
    return flask.render_template("status/waiting.html",
                                 number=len(list(tasks)),
                                 tasks=tasks, bg_tasks_cnt=bg_tasks_cnt)


@status_ns.route("/running/")
def running():
    tasks = builds_logic.BuildsLogic.get_build_tasks(
        helpers.StatusEnum("running")).limit(200)
    return flask.render_template("status/running.html",
                                 number=len(list(tasks)),
                                 tasks=tasks)


@status_ns.route("/importing/")
def importing():
    tasks = builds_logic.BuildsLogic.get_build_tasks(
        helpers.StatusEnum("importing"),
        background=False).limit(200)
    bg_tasks_cnt = builds_logic.BuildsLogic.get_build_tasks(
        helpers.StatusEnum("importing"),
        background=True).count()

    return flask.render_template("status/importing.html",
                                 number=len(list(tasks)),
                                 bg_tasks_cnt=bg_tasks_cnt,
                                 tasks=tasks)
