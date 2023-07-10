"""Manage the status file of the progress."""

import datetime
import glob
import html
import os.path
from enum import Enum
from typing import NamedTuple, Optional

import jinja2
import natsort
from ruamel.yaml.main import YAML

_WAITING_STATUS_NAME = "Waiting validation"
_WAITING_STATUS_DESCRIPTION = """<div class="sidebar-box"><p>You should validate that the generate images are correct ({generated_images}).<br />
    If the result is correct remove the <a href="./{name}/REMOVE_TO_CONTINUE" target="_blank"><code>REMOVE_TO_CONTINUE</code></a> file.</p>
<p>If not you can:<br />
    <ul>
        <li>Edit the generated image, then remove the <a href="./{name}/REMOVE_TO_CONTINUE" target="_blank"><code>REMOVE_TO_CONTINUE</code></a> file.</li>
        <li>Edit the <a href="./{name}/config.yaml" target="_blank"><code>config.yaml</code></a> file to change the parameters, then remove the generated files ({generated_images}) to force the regeneration.</li>
        <li>Edit the source images ({source_images}) then remove the corresponding generated files ({generated_images}) to force the regeneration.</li>
    </ul>
</p>
<p>In th <a href="./{name}/source" target="_blank"><code>source</code></a> folder you can also find some images postfixed by <code>-skew-corrected</code> that the source image where the skew correction is applied.</p>
<p>In the <a href="./{name}/histogram" target="_blank"><code>histogram</code></a> folder yu can find tow histogram of the source images, one of them use a logarithm scale.</p>
<p>In the <a href="./{name}/crop" target="_blank"><code>crop</code></a> folder you can find the images with the detected block used by the automatic crop.</p>
<p>In the <a href="./{name}/skew" target="_blank"><code>skew</code></a> golder you can find some images that represent the the shew detection will be based on.</p>
<p class="read-more"><a href="javascript:void(0)" class="button">Read More</a></p></div>"""
_WAITING_ASSISTED_SPLIT_DESCRIPTION = """<div class="sidebar-box"><p>You are in assisted split mode, in the step where you should choose should the splitting,</p>
<p>For that you should open the <a href="./{name}/config.yaml" target="_blank"><code>config.yaml</code></a> file,
in the section <code>assisted_split</code> you can find the list of the images that will be split, with a configuration like that.</p>
<pre><code>assisted_split:
- source: /source/z-563313-h/assisted-split/image-1.png
  destinations:
  - 4
  - 1
  limits:
  - name: VC
    type: image center
    value: 1777
    vertical: true
    margin: 0
</code></pre>
<ul>
<li><code>source</code> is the path (on the server) of the image to split</li>
<li><code>destinations</code> is the list of the destination pages of the split images</li>
<li><code>limits</code> is the list of the limits used to split the image</li>
<li><code>name</code> is the name of the auto detected limit that we can found on the generated image on the <a href="./{name}/" target="_blank">base</a> folder</li>
<li><code>type</code> is the type of detection</li>
<li><code>value</code> is the position where we will to split</li>
<li><code>vertical</code> is a boolean that indicate if the limit is vertical or horizontal</li>
<li><code>margin</code> is the margin of the limit, if it set we will "lost" the part of the image around the split position</li>
</ul>
<p>For each image we will have all the detected limits, you can choose the limit that you want to use for the split (you can modify it if needed), and remove the other entries.</p>
<p>When you have finished to choose the limits, you should save the edited <code>config.yaml</code> file, then remove the <a href="./{name}/REMOVE_TO_CONTINUE" target="_blank"><code>REMOVE_TO_CONTINUE</code></a> file to continue the process.</p>
<p class="read-more"><a href="javascript:void(0)" class="button">Read More</a></p></div>"""
_WAITING_TO_TRANSFORM_STATUS = "Waiting to transform"
_WAITING_TO_ASSISTED_SPLIT_STATUS = "Waiting to split"
_DONE_STATUS = "Done"
_WAITING_TO_FINALIZE_STATUS = "Waiting to finalize"


class _Folder(NamedTuple):
    nb_images: int
    status: str
    details: str


class JobType(Enum):
    """The type of job."""

    NONE = "None"
    TRANSFORM = "transform"
    ASSISTED_SPLIT = "assisted-split"
    FINALIZE = "finalize"
    DOWN = "down"
    CODE = "code"


class Status:
    """Manage the status file of the progress."""

    current_job_type = JobType.NONE
    current_job_list: list[str] = []

    def __init__(self, no_write: bool = False) -> None:
        """Construct."""

        self.no_write = no_write
        self._file = os.path.join(os.environ.get("SCAN_SOURCE_FOLDER", "/source"), "status.html")
        self._status: dict[str, _Folder] = {}
        self._codes: list[str] = []
        self._consume: list[str] = []
        self._global_status = "Starting..."
        self._global_status_update = datetime.datetime.utcnow().replace(microsecond=0)
        self._start_time = datetime.datetime.utcnow().replace(microsecond=0)
        self._last_scan = datetime.datetime.utcnow()
        self._current_folder: Optional[str] = None
        self.scan()

    def set_global_status(self, status: str) -> None:
        """Set the global status."""

        if self._global_status != status:
            self._global_status = status
            self._global_status_update = datetime.datetime.utcnow().replace(microsecond=0)

        self.write()

    def set_current_folder(self, name: Optional[str]) -> None:
        """Set the current folder."""

        if name is not None and name.endswith("/config.yaml"):
            name = os.path.basename(os.path.dirname(name))

        write = self._current_folder != name
        self._current_folder = name

        if write:
            self.write()

    def set_status(self, name: str, nb_images: int, status: str, details: str = "") -> None:
        """Set the status of a folder."""

        # Config file name
        if name.endswith("/config.yaml"):
            name = os.path.basename(os.path.dirname(name))
        if nb_images <= 0 and name in self._status:
            nb_images = self._status[name].nb_images
        self._status[name] = _Folder(nb_images, html.escape(status), details)

        if self.no_write:
            print(f"{name}: {status}")

        if not self.no_write:
            self.write()

    def scan(self) -> None:
        """Scan for changes for waiting documents."""

        self._last_scan = datetime.datetime.utcnow()

        codes_folder = os.environ.get("SCAN_CODES_FOLDER", "/scan-codes")
        if codes_folder[-1] != "/":
            codes_folder += "/"
        self._codes = [
            f[len(codes_folder) :]
            for f in glob.glob(os.path.join(codes_folder, "**"), recursive=True)
            if os.path.isfile(f)
        ]

        consume_folder = os.environ.get("SCAN_FINAL_FOLDER", "/destination")
        if consume_folder[-1] != "/":
            consume_folder += "/"
        self._consume = [
            f[len(consume_folder) :]
            for f in glob.glob(os.path.join(consume_folder, "**"), recursive=True)
            if os.path.isfile(f)
        ]

        source_folder = os.environ.get("SCAN_SOURCE_FOLDER", "/source")
        for name in list(self._status):
            if name != self._current_folder:
                if os.path.isdir(os.path.join(source_folder, name)):
                    self._update_status(name)
                else:
                    del self._status[name]

        for folder_name in glob.glob(os.path.join(source_folder, "*")):
            if os.path.isdir(folder_name):
                name = os.path.basename(folder_name)
                if name not in self._status:
                    self._update_status(name)

    def _update_status(self, name: str) -> None:
        yaml = YAML(typ="safe")
        source_folder = os.environ.get("SCAN_SOURCE_FOLDER", "/source")
        if os.path.exists(os.path.join(source_folder, name, "error.yaml")):
            with open(
                os.path.join(source_folder, name, "error.yaml"),
                encoding="utf-8",
            ) as error_file:
                error = yaml.load(error_file)

            self.set_status(
                name,
                -1,
                "Error: " + error["error"],
                "<p>Stacktrace:</p><p><code>"
                + "<br />".join(error["traceback"])
                + f'</code></p><p>Remove the <a href="./{name}/error.yaml" target="_blank"><code>error.yaml</code></a> file to retry.</p>',
            )
            return

        if os.path.exists(os.path.join(source_folder, name, "DONE")):
            self.set_status(name, -1, _DONE_STATUS)
            return

        if not os.path.exists(os.path.join(source_folder, name, "config.yaml")):
            len_folder = len(os.path.join(source_folder, name).rstrip("/")) + 1
            files = [
                f[len_folder:]
                for f in glob.glob(
                    os.path.join(source_folder, name, "**"),
                    recursive=True,
                )
                if os.path.isfile(f)
            ]
            files = [f'<a href="./{name}/{f}" target="_blank"><code>{f}</code></a>' for f in files]
            self.set_status(name, -1, "Missing config", ", ".join(files))
            return

        with open(
            os.path.join(source_folder, name, "config.yaml"),
            encoding="utf-8",
        ) as config_file:
            config = yaml.load(config_file)

        if config is None:
            self.set_status(name, -1, "Empty config")
            return

        if os.path.exists(os.path.join(source_folder, name, "REMOVE_TO_CONTINUE")):
            rerun = False
            for image in config["steps"][-1]["sources"]:
                if not os.path.exists(image):
                    rerun = True
            nb_images = -1
            if config.get("steps", []):
                nb_images = len(config["steps"][-1]["sources"])
            elif "sources" in config:
                nb_images = len(config["sources"])

            if rerun:
                if len(config["steps"]) >= 2:
                    self.set_status(name, nb_images, "Waiting to " + config["steps"][-2]["name"])
                else:
                    self.set_status(name, nb_images, _WAITING_TO_TRANSFORM_STATUS)
            else:
                len_folder = len(os.path.join(source_folder, name).rstrip("/")) + 1
                source_images = (
                    config["steps"][-2]["sources"] if len(config["steps"]) >= 2 else config["sources"]
                )
                generated_images = [f[len_folder:] for f in config["steps"][-1]["sources"]]
                self.set_status(
                    name,
                    nb_images,
                    _WAITING_STATUS_NAME,
                    (
                        _WAITING_ASSISTED_SPLIT_DESCRIPTION
                        if config["steps"][-1]["name"] == "split"
                        else _WAITING_STATUS_DESCRIPTION
                    ).format(
                        name=name,
                        source_images=", ".join(
                            [
                                f'<a href="./{name}/{f}" target="_blank"><code>{f}</code></a>'
                                for f in source_images
                            ]
                        ),
                        generated_images=", ".join(
                            [
                                f'<a href="./{name}/{f}" target="_blank"><code>{f}</code></a>'
                                for f in generated_images
                            ]
                        ),
                    ),
                )

        else:
            if len(config.get("steps", [])) >= 1:
                self.set_status(name, -1, "Waiting to " + config["steps"][-1]["name"])
            else:
                self.set_status(name, -1, _WAITING_TO_TRANSFORM_STATUS)

    def write(self) -> None:
        """Write the status file."""

        if self._last_scan < datetime.datetime.utcnow() - datetime.timedelta(minutes=1):
            self.scan()

        if self.no_write:
            return

        with open(self._file, "w", encoding="utf-8") as status_file:
            env = jinja2.Environment(
                loader=jinja2.FileSystemLoader(os.path.dirname(__file__)),
                autoescape=jinja2.select_autoescape(),
            )
            template = env.get_template("status.html")
            status_file.write(
                template.render(
                    global_status=self._global_status,
                    global_status_update=self._global_status_update,
                    current_folder=self._current_folder,
                    start_time=self._start_time,
                    datetime=datetime,
                    status=self._status,
                    sorted_status_key=natsort.natsorted(self._status.keys()),
                    codes=self._codes,
                    consume=self._consume,
                )
            )

    def get_next_job(self) -> tuple[Optional[str], JobType]:
        """Get the next job to do."""

        job_types = [
            (JobType.TRANSFORM, _WAITING_TO_TRANSFORM_STATUS),
            (JobType.ASSISTED_SPLIT, _WAITING_TO_ASSISTED_SPLIT_STATUS),
            (JobType.FINALIZE, _WAITING_TO_FINALIZE_STATUS),
        ]
        if os.environ.get("PROGRESS", "FALSE") != "TRUE":
            job_types.append((JobType.DOWN, _DONE_STATUS))

        for job_type, waiting_status in job_types:
            if not self.current_job_list:
                self.current_job_type = job_type
                for name, folder in self._status.items():
                    if folder.status == waiting_status:
                        self.current_job_list.append(name)

        if not self.current_job_list:
            self.current_job_type = JobType.CODE
            self.current_job_list = list(self._codes)

        if not self.current_job_list:
            self.current_job_type = JobType.NONE

        return self.current_job_list.pop(0) if self.current_job_list else None, self.current_job_type
