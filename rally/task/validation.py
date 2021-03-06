# Copyright 2014: Mirantis Inc.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may

#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import functools
import os
import re

from glanceclient import exc as glance_exc
from novaclient import exceptions as nova_exc
import six

from rally.common.i18n import _
from rally.common import logging
from rally.common import validation
from rally.common import yamlutils as yaml
from rally import exceptions
from rally.plugins.openstack.context.nova import flavors as flavors_ctx
from rally.plugins.openstack import types as openstack_types
from rally.task import types

LOG = logging.getLogger(__name__)

# TODO(astudenov): remove after deprecating all old validators
ValidationResult = validation.ValidationResult
add = validation.add


@validation.add("required_platform", platform="openstack", users=True)
@validation.configure(name="old_validator", namespace="openstack")
class OldValidator(validation.Validator):

    class Deployment(object):
        pass

    def __init__(self, fn, *args, **kwargs):
        """Legacy validator for OpenStack scenarios

        :param fn: function that performs validation
        """
        self.fn = fn
        self.args = args
        self.kwargs = kwargs

    def validate(self, credentials, config, plugin_cls, plugin_cfg):
        creds = credentials.get("openstack", {})
        users = creds.get("users", [])

        deployment = self.Deployment()
        deployment.get_credentials_for = credentials.get

        if users:
            users = [user["credential"].clients() for user in users]
            for clients in users:
                result = self._run_fn(config, deployment, clients)
                if not result.is_valid:
                    return result
            return ValidationResult(True)
        else:
            return self._run_fn(config, deployment)

    def _run_fn(self, config, deployment, clients=None):
        return (self.fn(config, clients, deployment,
                        *self.args, **self.kwargs) or ValidationResult(True))


def validator(fn):
    """Decorator that constructs a scenario validator from given function.

    Decorated function should return ValidationResult on error.

    :param fn: function that performs validation
    :returns: rally scenario validator
    """
    def wrap_given(*args, **kwargs):
        """Dynamic validation decorator for scenario.

        :param args: the arguments of the decorator of the benchmark scenario
        ex. @my_decorator("arg1"), then args = ("arg1",)
        :param kwargs: the keyword arguments of the decorator of the scenario
        ex. @my_decorator(kwarg1="kwarg1"), then kwargs = {"kwarg1": "kwarg1"}
        """
        def wrap_scenario(scenario):
            scenario._meta_setdefault("validators", [])
            scenario._meta_get("validators").append(
                ("old_validator", (fn, ) + args, kwargs))
            return scenario

        return wrap_scenario

    return wrap_given


def _file_access_ok(filename, mode, param_name, required=True):
    if not filename:
        return ValidationResult(not required,
                                "Parameter %s required" % param_name)
    if not os.access(os.path.expanduser(filename), mode):
        return ValidationResult(
            False, "Could not open %(filename)s with mode %(mode)s "
            "for parameter %(param_name)s"
            % {"filename": filename, "mode": mode, "param_name": param_name})
    return ValidationResult(True)


def check_command_dict(command):
    """Check command-specifying dict `command', raise ValueError on error."""

    if not isinstance(command, dict):
        raise ValueError("Command must be a dictionary")

    # NOTE(pboldin): Here we check for the values not for presence of the keys
    # due to template-driven configuration generation that can leave keys
    # defined but values empty.
    if command.get("interpreter"):
        script_file = command.get("script_file")
        if script_file:
            if "script_inline" in command:
                raise ValueError(
                    "Exactly one of script_inline or script_file with "
                    "interpreter is expected: %r" % command)
        # User tries to upload a shell? Make sure it is same as interpreter
        interpreter = command.get("interpreter")
        interpreter = (interpreter[-1]
                       if isinstance(interpreter, (tuple, list))
                       else interpreter)
        if (command.get("local_path") and
           command.get("remote_path") != interpreter):
            raise ValueError(
                "When uploading an interpreter its path should be as well"
                " specified as the `remote_path' string: %r" % command)
    elif not command.get("remote_path"):
        # No interpreter and no remote command to execute is given
        raise ValueError(
            "Supplied dict specifies no command to execute,"
            " either interpreter or remote_path is required: %r" % command)

    unexpected_keys = set(command) - set(["script_file", "script_inline",
                                          "interpreter", "remote_path",
                                          "local_path", "command_args"])
    if unexpected_keys:
        raise ValueError(
            "Unexpected command parameters: %s" % ", ".join(unexpected_keys))


@validator
def valid_command(config, clients, deployment, param_name, required=True):
    """Checks that parameter is a proper command-specifying dictionary.

    Ensure that the command dictionary is a proper command-specifying
    dictionary described in `vmtasks.VMTasks.boot_runcommand_delete' docstring.

    :param param_name: Name of parameter to validate
    :param required: Boolean indicating that the command dictionary is required
    """
    # TODO(amaretskiy): rework this validator into ResourceType, so this
    #                   will allow to validate parameters values as well

    command = config.get("args", {}).get(param_name)
    if command is None and not required:
        return ValidationResult(True)

    try:
        check_command_dict(command)
    except ValueError as e:
        return ValidationResult(False, str(e))

    for key in "script_file", "local_path":
        if command.get(key):
            return _file_access_ok(
                filename=command[key],
                mode=os.R_OK,
                param_name=param_name + "." + key,
                required=True)

    return ValidationResult(True)


def _get_validated_image(config, clients, param_name):
    image_context = config.get("context", {}).get("images", {})
    image_args = config.get("args", {}).get(param_name)
    image_ctx_name = image_context.get("image_name")

    if not image_args:
        msg = _("Parameter %s is not specified.") % param_name
        return (ValidationResult(False, msg), None)

    if "image_name" in image_context:
        # NOTE(rvasilets) check string is "exactly equal to" a regex
        # or image name from context equal to image name from args
        if "regex" in image_args:
            match = re.match(image_args.get("regex"), image_ctx_name)
        if image_ctx_name == image_args.get("name") or (
                "regex" in image_args and match):
            image = {
                "size": image_context.get("min_disk", 0),
                "min_ram": image_context.get("min_ram", 0),
                "min_disk": image_context.get("min_disk", 0)
            }
            return (ValidationResult(True), image)
    try:
        image_id = openstack_types.GlanceImage.transform(
            clients=clients, resource_config=image_args)
        image = clients.glance().images.get(image_id)
        if hasattr(image, "to_dict"):
            # NOTE(stpierre): Glance v1 images are objects that can be
            # converted to dicts; Glance v2 images are already
            # dict-like
            image = image.to_dict()
        if not image.get("size"):
            image["size"] = 0
        if not image.get("min_ram"):
            image["min_ram"] = 0
        if not image.get("min_disk"):
            image["min_disk"] = 0
        return (ValidationResult(True), image)
    except (glance_exc.HTTPNotFound, exceptions.InvalidScenarioArgument):
        message = _("Image '%s' not found") % image_args
        return (ValidationResult(False, message), None)


def _get_flavor_from_context(config, flavor_value):
    if "flavors" not in config.get("context", {}):
        raise exceptions.InvalidScenarioArgument("No flavors context")

    flavors = [flavors_ctx.FlavorConfig(**f)
               for f in config["context"]["flavors"]]
    resource = types.obj_from_name(resource_config=flavor_value,
                                   resources=flavors, typename="flavor")
    flavor = flavors_ctx.FlavorConfig(**resource)
    flavor.id = "<context flavor: %s>" % flavor.name
    return (ValidationResult(True), flavor)


def _get_validated_flavor(config, clients, param_name):
    flavor_value = config.get("args", {}).get(param_name)
    if not flavor_value:
        msg = "Parameter %s is not specified." % param_name
        return (ValidationResult(False, msg), None)
    try:
        flavor_id = openstack_types.Flavor.transform(
            clients=clients, resource_config=flavor_value)
        flavor = clients.nova().flavors.get(flavor=flavor_id)
        return (ValidationResult(True), flavor)
    except (nova_exc.NotFound, exceptions.InvalidScenarioArgument):
        try:
            return _get_flavor_from_context(config, flavor_value)
        except exceptions.InvalidScenarioArgument:
            pass
        message = _("Flavor '%s' not found") % flavor_value
        return (ValidationResult(False, message), None)


@validator
def validate_share_proto(config, clients, deployment):
    """Validates value of share protocol for creation of Manila share."""
    allowed = ("NFS", "CIFS", "GLUSTERFS", "HDFS", "CEPHFS", )
    share_proto = config.get("args", {}).get("share_proto")
    if six.text_type(share_proto).upper() not in allowed:
        message = _("Share protocol '%(sp)s' is invalid, allowed values are "
                    "%(allowed)s.") % {"sp": share_proto,
                                       "allowed": "', '".join(allowed)}
        return ValidationResult(False, message)


@validator
def flavor_exists(config, clients, deployment, param_name):
    """Returns validator for flavor

    :param param_name: defines which variable should be used
                       to get flavor id value.
    """
    return _get_validated_flavor(config, clients, param_name)[0]


@validator
def workbook_contains_workflow(config, clients, deployment, workbook,
                               workflow_name):
    """Validate that workflow exist in workbook when workflow is passed

    :param workbook: parameter containing the workbook definition
    :param workflow_name: parameter containing the workflow name
    """

    wf_name = config.get("args", {}).get(workflow_name)
    if wf_name:
        wb_path = config.get("args", {}).get(workbook)
        wb_path = os.path.expanduser(wb_path)
        file_result = _file_access_ok(config.get("args", {}).get(workbook),
                                      os.R_OK, workbook)
        if not file_result.is_valid:
            return file_result

        with open(wb_path, "r") as wb_def:
            wb_def = yaml.safe_load(wb_def)
            if wf_name not in wb_def["workflows"]:
                return ValidationResult(
                    False,
                    "workflow '{}' not found in the definition '{}'".format(
                        wf_name, wb_def))


# TODO(astudenov): remove deprecated validators in 1.0.0

def deprecated_validator(name, old_validator_name, rally_version):
    def decorator(*args, **kwargs):
        def wrapper(plugin):
            plugin_name = plugin.get_name()
            LOG.warning(
                "Plugin '%s' uses validator 'rally.task.validation.%s' which "
                "is deprecated in favor of '%s' (it should be used "
                "via new decorator 'rally.common.validation.add') in "
                "Rally v%s.",
                plugin_name, old_validator_name, name, rally_version)
            plugin._meta_setdefault("validators", [])
            plugin._meta_get("validators").append((name, args, kwargs,))
            return plugin
        return wrapper
    return decorator


_deprecated_platform_validator = deprecated_validator(
    "required_platform", "required_openstack", "0.10.0")

required_openstack = functools.partial(
    _deprecated_platform_validator, platform="openstack")

number = deprecated_validator("number", "number", "0.10.0")

image_exists = deprecated_validator("image_exists", "image_exists", "0.10.0")

external_network_exists = deprecated_validator("external_network_exists",
                                               "external_network_exists",
                                               "0.10.0")

required_neutron_extensions = deprecated_validator(
    "required_neutron_extensions", "required_neutron_extensions", "0.10.0")

image_valid_on_flavor = deprecated_validator("image_valid_on_flavor",
                                             "image_valid_on_flavor",
                                             "0.10.0")

required_clients = deprecated_validator("required_clients", "required_clients",
                                        "0.10.0")

required_services = deprecated_validator("required_services",
                                         "required_services", "0.10.0")

validate_heat_template = deprecated_validator("validate_heat_template",
                                              "validate_heat_template",
                                              "0.10.0")

restricted_parameters = deprecated_validator("restricted_parameters",
                                             "restricted_parameters",
                                             "0.10.0")

required_cinder_services = deprecated_validator("required_cinder_services",
                                                "required_cinder_services",
                                                "0.10.0")

required_api_versions = deprecated_validator("required_api_versions",
                                             "required_api_versions",
                                             "0.10.0")

required_contexts = deprecated_validator("required_contexts",
                                         "required_contexts",
                                         "0.10.0")

required_param_or_context = deprecated_validator("required_param_or_context",
                                                 "required_param_or_context",
                                                 "0.10.0")

volume_type_exists = deprecated_validator("volume_type_exists",
                                          "volume_type_exists",
                                          "0.10.0")

file_exists = deprecated_validator("file_exists", "file_exists", "0.10.0")
