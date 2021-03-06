from datetime import datetime
import json
import os
import time
import gzip
import shutil
import multiprocessing
from setproctitle import setproctitle

from ..exceptions import MockRemoteError, CoprWorkerError, VmError, NoVmAvailable
from ..job import BuildJob
from ..mockremote import MockRemote
from ..constants import BuildStatus, build_log_format
from ..helpers import register_build_result, get_redis_connection, get_redis_logger, \
    local_file_logger


# ansible_playbook = "ansible-playbook"

try:
    import fedmsg
except ImportError:
    # fedmsg is optional
    fedmsg = None


class Worker(multiprocessing.Process):
    def __init__(self, opts, frontend_client, vm_manager, worker_id, vm, job):
        multiprocessing.Process.__init__(self, name="worker-{}".format(worker_id))

        self.opts = opts
        self.frontend_client = frontend_client
        self.vm_manager = vm_manager
        self.worker_id = worker_id
        self.vm = vm
        self.job = job

        self.log = get_redis_logger(self.opts, self.name, "worker")

    @property
    def name(self):
        return "backend.worker-{}-{}".format(self.worker_id, self.group_name)

    @property
    def group_name(self):
        try:
            return self.opts.build_groups[self.vm.group]["name"]
        except Exception as error:
            self.log.exception("Failed to get builder group name from config, using group_id as name."
                               "Original error: {}".format(error))
            return str(self.vm.group)

    def fedmsg_notify(self, topic, template, content=None):
        """
        Publish message to fedmsg bus when it is available
        :param topic:
        :param template:
        :param content:
        """
        if self.opts.fedmsg_enabled and fedmsg:
            content = content or {}
            content["who"] = self.name
            content["what"] = template.format(**content)

            try:
                fedmsg.publish(modname="copr", topic=topic, msg=content)
            # pylint: disable=W0703
            except Exception as e:
                self.log.exception("failed to publish message: {0}".format(e))

    def _announce_start(self, job):
        """
        Announce everywhere that a build process started now.
        """
        job.started_on = time.time()
        self.mark_started(job)

        template = "build start: user:{user} copr:{copr}" \
            "pkg: {pkg} build:{build} ip:{ip}  pid:{pid}"

        content = dict(user=job.submitter, copr=job.project_name,
                       owner=job.project_owner, pkg=job.package_name,
                       build=job.build_id, ip=self.vm.vm_ip, pid=self.pid)
        self.fedmsg_notify("build.start", template, content)

        template = "chroot start: chroot:{chroot} user:{user}" \
            "copr:{copr} pkg: {pkg} build:{build} ip:{ip}  pid:{pid}"

        content = dict(chroot=job.chroot, user=job.submitter,
                       owner=job.project_owner, pkg=job.package_name,
                       copr=job.project_name, build=job.build_id,
                       ip=self.vm.vm_ip, pid=self.pid)

        self.fedmsg_notify("chroot.start", template, content)

    def _announce_end(self, job):
        """
        Announce everywhere that a build process ended now.
        """
        job.ended_on = time.time()

        self.return_results(job)
        self.log.info("worker finished build: {0}".format(self.vm.vm_ip))
        template = "build end: user:{user} copr:{copr} build:{build}" \
            "  pkg: {pkg}  version: {version} ip:{ip}  pid:{pid} status:{status}"

        content = dict(user=job.submitter, copr=job.project_name,
                       owner=job.project_owner,
                       pkg=job.package_name, version=job.package_version,
                       build=job.build_id, ip=self.vm.vm_ip, pid=self.pid,
                       status=job.status, chroot=job.chroot)
        self.fedmsg_notify("build.end", template, content)

    def mark_started(self, job):
        """
        Send data about started build to the frontend
        """
        job.status = BuildStatus.RUNNING
        build = job.to_dict()
        self.log.info("starting build: {}".format(build))

        data = {"builds": [build]}
        try:
            self.frontend_client.update(data)
        except:
            raise CoprWorkerError("Could not communicate to front end to submit status info")

    def return_results(self, job):
        """
        Send the build results to the frontend
        """
        self.log.info("Build {} finished with status {}. Took {} seconds"
                      .format(job.build_id, job.status, job.ended_on - job.started_on))

        data = {"builds": [job.to_dict()]}

        try:
            self.frontend_client.update(data)
        except Exception as err:
            raise CoprWorkerError(
                "Could not communicate to front end to submit results: {}"
                .format(err)
            )

    @classmethod
    def pkg_built_before(cls, pkg, chroot, destdir):
        """
        Check whether the package has already been built in this chroot.
        """
        s_pkg = os.path.basename(pkg)
        pdn = s_pkg.replace(".src.rpm", "")
        resdir = "{0}/{1}/{2}".format(destdir, chroot, pdn)
        resdir = os.path.normpath(resdir)
        if os.path.exists(resdir) and os.path.exists(os.path.join(resdir, "success")):
            return True
        return False

    def init_fedmsg(self):
        """
        Initialize Fedmsg (this assumes there are certs and a fedmsg config on disk).
        """
        if not (self.opts.fedmsg_enabled and fedmsg):
            return

        try:
            fedmsg.init(name="relay_inbound", cert_prefix="copr", active=True)
        except Exception as e:
            self.log.exception("Failed to initialize fedmsg: {}".format(e))

    # TODO: doing skip logic on fronted during @start_build query
    # def on_pkg_skip(self, job):
    #     """
    #     Handle package skip
    #     """
    #     self._announce_start(job)
    #     self.log.info("Skipping: package {} has been already built before.".format(job.pkg))
    #     job.status = BuildStatus.SKIPPED
    #     self._announce_end(job)

    def do_job(self, job):
        """
        Executes new job.

        :param job: :py:class:`~backend.job.BuildJob`
        """

        self._announce_start(job)
        self.update_process_title(suffix="Task: {} chroot: {} build started"
                                  .format(job.build_id, job.chroot))
        status = BuildStatus.SUCCEEDED

        # setup our target dir locally
        if not os.path.exists(job.chroot_dir):
            try:
                os.makedirs(job.chroot_dir)
            except (OSError, IOError):
                self.log.exception("Could not make results dir for job: {}"
                                   .format(job.chroot_dir))
                status = BuildStatus.FAILURE

        self.clean_result_directory(job)

        if status == BuildStatus.SUCCEEDED:
            # FIXME
            # need a plugin hook or some mechanism to check random
            # info about the pkgs
            # this should use ansible to download the pkg on
            # the remote system
            # and run a series of checks on the package before we
            # start the build - most importantly license checks.

            self.log.info("Starting build: id={} builder={} job: {}"
                          .format(job.build_id, self.vm.vm_ip, job))

            with local_file_logger(
                "{}.builder.mr".format(self.name),
                job.chroot_log_path,
                fmt=build_log_format) as build_logger:
                try:
                    mr = MockRemote(
                        builder_host=self.vm.vm_ip,
                        job=job,
                        logger=build_logger,
                        opts=self.opts
                    )
                    mr.check()

                    build_details = mr.build_pkg_and_process_results()
                    job.update(build_details)

                    if self.opts.do_sign:
                        mr.add_pubkey()

                    register_build_result(self.opts)

                except MockRemoteError as e:
                    # record and break
                    self.log.exception(
                        "Error during the build, host={}, build_id={}, chroot={}, error: {}"
                        .format(self.vm.vm_ip, job.build_id, job.chroot, e)
                    )
                    status = BuildStatus.FAILURE
                    register_build_result(self.opts, failed=True)

            self.log.info(
                "Finished build: id={} builder={} timeout={} destdir={}"
                " chroot={} repos={}"
                .format(job.build_id, self.vm.vm_ip, job.timeout, job.destdir,
                        job.chroot, str(job.repos)))

            self.copy_mock_logs(job)

        job.status = status
        self._announce_end(job)
        self.update_process_title(suffix="Task: {} chroot: {} done"
                                  .format(job.build_id, job.chroot))

    def copy_mock_logs(self, job):
        if not os.path.isdir(job.results_dir):
            self.log.info("Job results dir doesn't exists, couldn't copy main log; path: {}"
                          .format(job.results_dir))
            return

        log_names = [(job.chroot_log_name, "mockchain.log.gz"),
                     (job.rsync_log_name, "rsync.log.gz")]

        for src_name, dst_name in log_names:
            src = os.path.join(job.chroot_dir, src_name)
            dst = os.path.join(job.results_dir, dst_name)
            try:
                with open(src, "rb") as f_src, gzip.open(dst, "wb") as f_dst:
                    f_dst.writelines(f_src)
            except IOError:
                self.log.info("File {} not found".format(src))

    def clean_result_directory(self, job):
        """
        Create backup directory and move there results from previous build.
        """
        if not os.path.exists(job.results_dir) or os.listdir(job.results_dir) == []:
            return

        backup_dir_name = "prev_build_backup"
        backup_dir = os.path.join(job.results_dir, backup_dir_name)
        self.log.info("Cleaning target directory, results from previous build storing in {}"
                      .format(backup_dir))

        if not os.path.exists(backup_dir):
            os.makedirs(backup_dir)

        files = (x for x in os.listdir(job.results_dir) if x != backup_dir_name)
        for filename in files:
            file_path = os.path.join(job.results_dir, filename)
            if os.path.isfile(file_path):
                if file_path.endswith((".info", ".log", ".log.gz")):
                    os.rename(file_path, os.path.join(backup_dir, filename))

                elif not file_path.endswith(".rpm"):
                    os.remove(file_path)
            else:
                shutil.rmtree(file_path)

    def update_process_title(self, suffix=None):
        title = "Worker-{}-{} ".format(self.worker_id, self.group_name)
        title += "vm.vm_ip={} ".format(self.vm.vm_ip)
        title += "vm.vm_name={} ".format(self.vm.vm_name)
        if suffix:
            title += str(suffix)
        setproctitle(title)

    def run(self):
        self.log.info("Starting worker")
        self.init_fedmsg()

        try:
            self.do_job(self.job)
        except VmError as error:
            self.log.exception("Building error: {}".format(error))
        finally:
            self.vm_manager.release_vm(self.vm.vm_name)
