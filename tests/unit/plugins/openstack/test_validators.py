# Copyright 2017: Mirantis Inc.
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

import copy
import ddt
import mock

from glanceclient import exc as glance_exc
from novaclient import exceptions as nova_exc

from rally import consts
from rally import exceptions
from rally.plugins.openstack import validators
from tests.unit import test


credentials = {
    "openstack": {
        "admin": mock.MagicMock(),
        "users": [mock.MagicMock()],
    }
}

config = dict(args={"image": {"id": "fake_id",
                              "min_ram": 10,
                              "size": 1024 ** 3,
                              "min_disk": 10.0 * (1024 ** 3),
                              "image_name": "foo_image"},
                    "flavor": {"id": "fake_flavor_id",
                               "name": "test"},
                    "foo_image": {"id": "fake_image_id"}
                    },
              context={"images": {"image_name": "foo_image"},
                       "api_versions": mock.MagicMock()}
              )


@ddt.ddt
class ImageExistsValidatorTestCase(test.TestCase):

    def setUp(self):
        super(ImageExistsValidatorTestCase, self).setUp()
        self.validator = validators.ImageExistsValidator("image", True)
        self.config = copy.deepcopy(config)
        self.credentials = copy.deepcopy(credentials)

    @ddt.unpack
    @ddt.data(
        {"param_name": "fake_param", "nullable": True, "err_msg": None},
        {"param_name": "fake_param", "nullable": False,
         "err_msg": "Parameter fake_param is not specified."},
        {"param_name": "image", "nullable": True, "err_msg": None},
    )
    def test_validator(self, param_name, nullable, err_msg, ex=False):
        validator = validators.ImageExistsValidator(param_name,
                                                    nullable)

        clients = self.credentials[
            "openstack"]["users"][0].clients.return_value

        clients.glance().images.get = mock.Mock()
        if ex:
            clients.glance().images.get.side_effect = ex

        result = validator.validate(self.config, self.credentials, None, None)

        if err_msg:
            self.assertEqual(err_msg, result.msg)
        elif result:
            self.assertIsNone(result, "Unexpected result '%s'" % result.msg)

    def test_validator_image_from_context(self):
        config = {"args": {
            "image": {"regex": r"^foo$"}}, "context": {
            "images": {
                "image_name": "foo"}}}

        result = self.validator.validate(config, self.credentials, None, None)
        self.assertIsNone(result)

    @mock.patch("rally.plugins.openstack.validators"
                ".openstack_types.GlanceImage.transform",
                return_value="image_id")
    def test_validator_image_not_in_context(self, mock_glance_image_transform):
        config = {"args": {
            "image": "fake_image"}, "context": {
            "images": {
                "fake_image_name": "foo"}}}

        clients = self.credentials[
            "openstack"]["users"][0].get.return_value.clients.return_value
        clients.glance().images.get = mock.Mock()

        result = self.validator.validate(config, self.credentials, None, None)
        self.assertIsNone(result)

        mock_glance_image_transform.assert_called_once_with(
            clients=clients, resource_config=config["args"]["image"])
        clients.glance().images.get.assert_called_with("image_id")

        exs = [exceptions.InvalidScenarioArgument(),
               glance_exc.HTTPNotFound()]
        for ex in exs:
            clients.glance().images.get.side_effect = ex

            result = self.validator.validate(config, self.credentials,
                                             None, None)

            self.assertEqual("Image 'fake_image' not found", result.msg)


@ddt.ddt
class ExternalNetworkExistsValidatorTestCase(test.TestCase):

    def setUp(self):
        super(ExternalNetworkExistsValidatorTestCase, self).setUp()
        self.validator = validators.ExternalNetworkExistsValidator("net")
        self.config = copy.deepcopy(config)
        self.credentials = copy.deepcopy(credentials)

    @ddt.unpack
    @ddt.data(
        {"foo_conf": {}},
        {"foo_conf": {"args": {"net": "custom"}}},
        {"foo_conf": {"args": {"net": "non_exist"}},
         "err_msg": "External (floating) network with name non_exist"
                    " not found by user {}. Available networks:"
                    " [{}, {}]"},
        {"foo_conf": {"args": {"net": "custom"}},
         "net1_name": {"name": {"net": "public"}},
         "net2_name": {"name": {"net": "custom"}},
         "err_msg": "External (floating) network with name custom"
                    " not found by user {}. Available networks:"
                    " [{}, {}]"}
    )
    def test_validator(self, foo_conf, net1_name="public", net2_name="custom",
                       err_msg=""):

        user = self.credentials["openstack"]["users"][0]

        net1 = {"name": net1_name, "router:external": True}
        net2 = {"name": net2_name, "router:external": True}

        user["credential"].clients().neutron().list_networks.return_value = {
            "networks": [net1, net2]}

        result = self.validator.validate(foo_conf, self.credentials,
                                         None, None)
        if err_msg:
            self.assertTrue(result)
            self.assertEqual(err_msg.format(user["credential"].username,
                                            net1, net2), result.msg[0])
        elif result:
            self.assertIsNone(result, "Unexpected result '%s'" % result)


@ddt.ddt
class RequiredNeutronExtensionsValidatorTestCase(test.TestCase):

    def setUp(self):
        super(RequiredNeutronExtensionsValidatorTestCase, self).setUp()
        self.config = copy.deepcopy(config)
        self.credentials = copy.deepcopy(credentials)

    @ddt.unpack
    @ddt.data(
        {"ext_validate": "existing_extension"},
        {"ext_validate": "absent_extension",
         "err_msg": "Neutron extension absent_extension is not configured"}
    )
    def test_validator(self, ext_validate, err_msg=False):
        validator = validators.RequiredNeutronExtensionsValidator(
            ext_validate)
        clients = self.credentials["openstack"]["users"][0][
            "credential"].clients()

        clients.neutron().list_extensions.return_value = {
            "extensions": [{"alias": "existing_extension"}]}
        result = validator.validate({}, self.credentials, None, None)

        if err_msg:
            self.assertTrue(result)
            self.assertEqual(err_msg, result.msg)
        else:
            self.assertIsNone(result)


@ddt.ddt
class ImageValidOnFlavorValidatorTestCase(test.TestCase):

    def setUp(self):
        super(ImageValidOnFlavorValidatorTestCase, self).setUp()
        self.validator = validators.ImageValidOnFlavorValidator("foo_flavor",
                                                                "image")
        self.config = copy.deepcopy(config)
        self.credentials = copy.deepcopy(credentials)

    @ddt.unpack
    @ddt.data(
        {"flavor_ram": 15, "flavor_disk": 15.0 * (1024 ** 3), "err_msg": None},
        {"flavor_ram": 5, "flavor_disk": 5.0 * (1024 ** 3),
         "err_msg": "The memory size for flavor '%s' is too small"
                    " for requested image 'fake_id'"},
        {"flavor_ram": 15, "flavor_disk": 5.0 / (1024 ** 3),
         "err_msg": "The disk size for flavor '%s' is too small"
                    " for requested image 'fake_id'"},
        {"flavor_ram": 15, "flavor_disk": 5.0 * (1024 ** 3),
         "err_msg": "The minimal disk size for flavor '%s' is too small"
                    " for requested image 'fake_id'"},
    )
    def test_validator(self, flavor_ram, flavor_disk, err_msg):
        image = config["args"]["image"]
        flavor = mock.Mock(ram=flavor_ram, disk=flavor_disk)

        success = validators.ValidationResult(True)

        user = self.credentials["openstack"]["users"][0]["credential"]
        user.clients().nova().flavors.get.return_value = "foo_flavor"

        self.validator._get_validated_image = mock.Mock()
        self.validator._get_validated_image.return_value = (success, image)

        self.validator._get_validated_flavor = mock.Mock()
        self.validator._get_validated_flavor.return_value = (success, flavor)

        result = self.validator.validate(config, self.credentials, None, None)

        if err_msg:
            self.assertEqual(err_msg % flavor.id, result.msg)
        else:
            self.assertIsNone(result, "Unexpected message")

    @mock.patch(
        "rally.plugins.openstack.validators"
        ".ImageValidOnFlavorValidator._get_validated_flavor")
    @mock.patch(
        "rally.plugins.openstack.validators"
        ".ImageValidOnFlavorValidator._get_validated_image")
    def test_validator_incorrect_result(self, mock__get_validated_image,
                                        mock__get_validated_flavor):

        validator = validators.ImageValidOnFlavorValidator(
            "foo_flavor", "image", fail_on_404_image=False)

        image = self.config["args"]["image"]
        flavor = mock.Mock(ram=15, disk=15.0 * (1024 ** 3))

        success = validators.ValidationResult(True, "Success")
        fail = validators.ValidationResult(False, "Not success")

        user = self.credentials["openstack"]["users"][0]["credential"]
        user.clients().nova().flavors.get.return_value = "foo_flavor"

        # Flavor is incorrect
        mock__get_validated_flavor.return_value = (fail, flavor)
        result = validator.validate(self.config, self.credentials, None, None)

        self.assertIsNotNone(result)
        self.assertEqual("Not success", result.msg)

        # image is incorrect
        user.clients().nova().flavors.get.return_value = "foo_flavor"
        mock__get_validated_flavor.reset_mock()
        mock__get_validated_flavor.return_value = (success, flavor)
        mock__get_validated_image.return_value = (success, None)
        result = validator.validate(self.config, self.credentials, None, None)
        self.assertIsNone(result)
        mock__get_validated_image.reset_mock()
        mock__get_validated_image.return_value = (fail, image)
        result = validator.validate(self.config, self.credentials, None, None)
        self.assertIsNotNone(result)
        self.assertEqual("Not success", result.msg)
        # 'fail_on_404_image' == True
        result = self.validator.validate(self.config, self.credentials,
                                         None, None)
        self.assertIsNotNone(result)
        self.assertEqual("Not success", result.msg)
        # 'validate_disk' = False
        validator = validators.ImageValidOnFlavorValidator(
            "foo_flavor", "image", validate_disk=False)
        mock__get_validated_image.reset_mock()
        mock__get_validated_image.return_value = (success, image)
        result = validator.validate(self.config, self.credentials, None, None)
        self.assertIsNone(result)

    def test__get_validated_flavor_wrong_value_in_config(self):

        result = self.validator._get_validated_flavor(self.config,
                                                      self.credentials,
                                                      "foo_flavor")
        self.assertEqual("Parameter foo_flavor is not specified.",
                         result[0].msg)

    @mock.patch("rally.plugins.openstack.validators"
                ".openstack_types.Flavor.transform",
                return_value="flavor_id")
    def test__get_validated_flavor(self, mock_flavor_transform):

        clients = mock.Mock()
        clients.nova().flavors.get.return_value = "flavor"

        result = self.validator._get_validated_flavor(self.config,
                                                      clients,
                                                      "flavor")
        self.assertTrue(result[0].is_valid, result[0].msg)
        self.assertEqual(result[1], "flavor")

        mock_flavor_transform.assert_called_once_with(
            clients=clients, resource_config=self.config["args"]["flavor"])
        clients.nova().flavors.get.assert_called_once_with(flavor="flavor_id")

        clients.side_effect = exceptions.InvalidScenarioArgument("")
        result = self.validator._get_validated_flavor(self.config,
                                                      clients,
                                                      "flavor")
        self.assertTrue(result[0].is_valid, result[0].msg)
        self.assertEqual(result[1], "flavor")
        mock_flavor_transform.assert_called_with(
            clients=clients, resource_config=self.config["args"]["flavor"])
        clients.nova().flavors.get.assert_called_with(flavor="flavor_id")

    @mock.patch("rally.plugins.openstack.validators"
                ".openstack_types.Flavor.transform")
    def test__get_validated_flavor_not_found(self, mock_flavor_transform):

        clients = mock.MagicMock()
        clients.nova().flavors.get.side_effect = nova_exc.NotFound("")

        result = self.validator._get_validated_flavor(self.config,
                                                      clients,
                                                      "flavor")
        self.assertFalse(result[0].is_valid, result[0].msg)
        self.assertEqual("Flavor '%s' not found" %
                         self.config["args"]["flavor"],
                         result[0].msg)
        mock_flavor_transform.assert_called_once_with(
            clients=clients, resource_config=self.config["args"]["flavor"])

    @mock.patch("rally.plugins.openstack.validators"
                ".openstack_types.GlanceImage.transform",
                return_value="image_id")
    def test__get_validated_image(self, mock_glance_image_transform):
        image = {
            "size": 0,
            "min_ram": 0,
            "min_disk": 0
        }
        # Get image name from context
        result = self.validator._get_validated_image({"args": {
            "image": {"regex": r"^foo$"}}, "context": {
            "images": {
                "image_name": "foo"}
        }}, self.credentials, "image")
        self.assertIsInstance(result[0], validators.ValidationResult)
        self.assertTrue(result[0].is_valid)
        self.assertEqual(result[0].msg, "")
        self.assertEqual(result[1], image)

        clients = mock.Mock()
        clients.glance().images.get().to_dict.return_value = {
            "image": "image_id"}
        image["image"] = "image_id"

        result = self.validator._get_validated_image(self.config,
                                                     clients,
                                                     "image")
        self.assertTrue(result[0].is_valid, result[0].msg)
        self.assertEqual(image, result[1])
        mock_glance_image_transform.assert_called_once_with(
            clients=clients, resource_config=self.config["args"]["image"])
        clients.glance().images.get.assert_called_with("image_id")

    @mock.patch("rally.plugins.openstack.validators"
                ".openstack_types.GlanceImage.transform",
                return_value="image_id")
    def test__get_validated_image_incorrect_param(self,
                                                  mock_glance_image_transform):
        # Wrong 'param_name'
        result = self.validator._get_validated_image(self.config,
                                                     self.credentials,
                                                     "fake_param")
        self.assertIsInstance(result[0], validators.ValidationResult)
        self.assertFalse(result[0].is_valid)
        self.assertEqual(result[0].msg,
                         "Parameter fake_param is not specified.")
        self.assertIsNone(result[1])

        # 'image_name' is not in 'image_context'
        image = {"id": "image_id", "size": 1024,
                 "min_ram": 256, "min_disk": 512}

        clients = mock.Mock()
        clients.glance().images.get().to_dict.return_value = image
        config = {"args": {"image": "foo_image",
                           "context": {"images": {
                               "fake_parameter_name": "foo_image"}
                           }}
                  }
        result = self.validator._get_validated_image(config,
                                                     clients,
                                                     "image")
        self.assertIsNotNone(result)
        self.assertTrue(result[0].is_valid)
        self.assertEqual(result[1], image)

        mock_glance_image_transform.assert_called_once_with(
            clients=clients, resource_config=config["args"]["image"])
        clients.glance().images.get.assert_called_with("image_id")

    @mock.patch("rally.plugins.openstack.validators"
                ".openstack_types.GlanceImage.transform",
                return_value="image_id")
    def test__get_validated_image_exceptions(self,
                                             mock_glance_image_transform):
        clients = mock.Mock()
        clients.glance().images.get.return_value = "image"
        clients.glance().images.get.side_effect = glance_exc.HTTPNotFound("")
        result = self.validator._get_validated_image(config,
                                                     clients,
                                                     "image")
        self.assertIsInstance(result[0], validators.ValidationResult)
        self.assertFalse(result[0].is_valid)
        self.assertEqual(result[0].msg,
                         "Image '%s' not found" % config["args"]["image"])
        self.assertIsNone(result[1])
        mock_glance_image_transform.assert_called_once_with(
            clients=clients, resource_config=config["args"]["image"])
        clients.glance().images.get.assert_called_with("image_id")

        clients.side_effect = exceptions.InvalidScenarioArgument("")
        result = self.validator._get_validated_image(config,
                                                     clients,
                                                     "image")
        self.assertIsInstance(result[0], validators.ValidationResult)
        self.assertFalse(result[0].is_valid)
        self.assertEqual(result[0].msg,
                         "Image '%s' not found" % config["args"]["image"])
        self.assertIsNone(result[1])
        mock_glance_image_transform.assert_called_with(
            clients=clients, resource_config=config["args"]["image"])
        clients.glance().images.get.assert_called_with("image_id")

    @mock.patch("rally.plugins.openstack.validators"
                ".types.obj_from_name")
    @mock.patch("rally.plugins.openstack.validators"
                ".flavors_ctx.FlavorConfig")
    def test__get_flavor_from_context(self, mock_flavor_config,
                                      mock_obj_from_name):
        config = {"context": {"images": {"fake_parameter_name": "foo_image"},
                              }
                  }

        self.assertRaises(exceptions.InvalidScenarioArgument,
                          self.validator._get_flavor_from_context,
                          config, "foo_flavor")

        config = {"context": {"images": {"fake_parameter_name": "foo_image"},
                              "flavors": [{"flavor1": "fake_flavor1"}]}
                  }
        result = self.validator._get_flavor_from_context(config, "foo_flavor")

        self.assertIsInstance(result[0], validators.ValidationResult)
        self.assertTrue(result[0].is_valid)
        self.assertEqual("<context flavor: %s>" % result[1].name, result[1].id)


class RequiredClientsValidatorTestCase(test.TestCase):

    def setUp(self):
        super(RequiredClientsValidatorTestCase, self).setUp()
        self.config = copy.deepcopy(config)
        self.credentials = copy.deepcopy(credentials)

    def test_validate(self):
        validator = validators.RequiredClientsValidator(components=["keystone",
                                                                    "nova"])
        clients = self.credentials[
            "openstack"]["users"][0]["credential"].clients.return_value

        result = validator.validate(self.config, self.credentials, None, None)
        self.assertIsNone(result)

        clients.nova.side_effect = ImportError
        result = validator.validate(self.config, self.credentials, None, None)
        self.assertIsNotNone(result)
        self.assertEqual("Client for nova is not installed. To install it "
                         "run `pip install python-novaclient`", result.msg)

    def test_validate_with_admin(self):
        validator = validators.RequiredClientsValidator(components=["keystone",
                                                                    "nova"],
                                                        admin=True)
        clients = self.credentials[
            "openstack"]["admin"].clients.return_value
        result = validator.validate(self.config, self.credentials, None, None)
        self.assertIsNone(result)

        clients.keystone.side_effect = ImportError
        result = validator.validate(self.config, self.credentials, None, None)
        self.assertIsNotNone(result)
        self.assertEqual("Client for keystone is not installed. To install it "
                         "run `pip install python-keystoneclient`", result.msg)


class RequiredServicesValidatorTestCase(test.TestCase):

    def setUp(self):
        super(RequiredServicesValidatorTestCase, self).setUp()
        self.validator = validators.RequiredServicesValidator([
            consts.Service.KEYSTONE,
            consts.Service.NOVA,
            consts.Service.NOVA_NET])
        self.config = config
        self.credentials = credentials

    def test_validator(self):

        self.config["context"]["api_versions"].get = mock.Mock(
            return_value={consts.Service.KEYSTONE: "service_type"})

        clients = self.credentials["openstack"]["admin"].clients()

        clients.services().values.return_value = [
            consts.Service.KEYSTONE, consts.Service.NOVA,
            consts.Service.NOVA_NET]
        fake_service = mock.Mock(binary="nova-network", status="enabled")
        clients.nova.services.list.return_value = [fake_service]
        result = self.validator.validate(self.config, self.credentials,
                                         None, None)
        self.assertIsNone(result)

        fake_service = mock.Mock(binary="keystone", status="enabled")
        clients.nova.services.list.return_value = [fake_service]
        result = self.validator.validate(self.config, self.credentials,
                                         None, None)
        self.assertIsNone(result)

        fake_service = mock.Mock(binary="nova-network", status="disabled")
        clients.nova.services.list.return_value = [fake_service]
        result = self.validator.validate(self.config, self.credentials,
                                         None, None)
        self.assertIsNone(result)

        validator = validators.RequiredServicesValidator([
            consts.Service.NOVA])
        clients.services().values.return_value = [
            consts.Service.KEYSTONE]

        result = validator.validate(self.config, self.credentials, None, None)
        self.assertIsNotNone(result)
        expected_msg = ("'{0}' service is not available. Hint: If '{0}'"
                        " service has non-default service_type, try to setup"
                        " it via 'api_versions' context.").format(
            consts.Service.NOVA)
        self.assertEqual(expected_msg, result.msg)

    def test_validator_wrong_service(self):

        self.config["context"]["api_versions"].get = mock.Mock(
            return_value={consts.Service.KEYSTONE: "service_type",
                          consts.Service.NOVA: "service_name"})

        clients = self.credentials["openstack"]["admin"].clients()
        clients.services().values.return_value = [
            consts.Service.KEYSTONE, consts.Service.NOVA]

        validator = validators.RequiredServicesValidator([
            consts.Service.KEYSTONE,
            consts.Service.NOVA, "lol"])

        result = validator.validate({}, self.credentials, None, None)
        self.assertIsNotNone(result)
        expected_msg = ("'{0}' service is not available. Hint: If '{0}'"
                        " service has non-default service_type, try to setup"
                        " it via 'api_versions' context.").format("lol")
        self.assertEqual(expected_msg, result.msg)


@ddt.ddt
class ValidateHeatTemplateValidatorTestCase(test.TestCase):

    def setUp(self):
        super(ValidateHeatTemplateValidatorTestCase, self).setUp()
        self.validator = validators.ValidateHeatTemplateValidator(
            "template_path1", "template_path2")
        self.config = copy.deepcopy(config)
        self.credentials = copy.deepcopy(credentials)

    @ddt.data(
        {"exception_msg": "Heat template validation failed on fake_path1. "
                          "Original error message: fake_msg."},
        {"exception_msg": None}
    )
    @ddt.unpack
    @mock.patch("rally.plugins.openstack.validators.os.path.exists",
                return_value=True)
    @mock.patch("rally.plugins.openstack.validators.open",
                side_effect=mock.mock_open(), create=True)
    def test_validate(self, mock_open, mock_exists, exception_msg):
        clients = self.credentials["openstack"]["users"][0][
            "credential"].clients()
        mock_open().__enter__().read.side_effect = ["fake_template1",
                                                    "fake_template2"]
        heat_validator = mock.MagicMock()
        if exception_msg:
            heat_validator.side_effect = Exception("fake_msg")
        clients.heat().stacks.validate = heat_validator
        context = {"args": {"template_path1": "fake_path1",
                            "template_path2": "fake_path2"}}
        result = self.validator.validate(context, self.credentials, None, None)

        if not exception_msg:
            heat_validator.assert_has_calls([
                mock.call(template="fake_template1"),
                mock.call(template="fake_template2")
            ])
            mock_open.assert_has_calls([
                mock.call("fake_path1", "r"),
                mock.call("fake_path2", "r")
            ], any_order=True)
            self.assertIsNone(result)
        else:
            heat_validator.assert_called_once_with(
                template="fake_template1")
            self.assertIsNotNone(result)
            self.assertEqual(
                "Heat template validation failed on fake_path1."
                " Original error message: fake_msg.", result.msg)

    def test_validate_missed_params(self):
        validator = validators.ValidateHeatTemplateValidator(
            params="fake_param")

        result = validator.validate(self.config, self.credentials, None, None)

        expected_msg = ("Path to heat template is not specified. Its needed "
                        "for heat template validation. Please check the "
                        "content of `fake_param` scenario argument.")
        self.assertIsNotNone(result)
        self.assertEqual(expected_msg, result.msg)

    @mock.patch("rally.plugins.openstack.validators.os.path.exists",
                return_value=False)
    def test_validate_file_not_found(self, mock_exists):
        context = {"args": {"template_path1": "fake_path1",
                            "template_path2": "fake_path2"}}
        result = self.validator.validate(context, self.credentials, None, None)
        expected_msg = "No file found by the given path fake_path1"
        self.assertIsNotNone(result)
        self.assertEqual(expected_msg, result.msg)


class RequiredCinderServicesValidatorTestCase(test.TestCase):

    def setUp(self):
        super(RequiredCinderServicesValidatorTestCase, self).setUp()
        self.credentials = copy.deepcopy(credentials)
        self.config = copy.deepcopy(config)

    def test_validate(self):
        validator = validators.RequiredCinderServicesValidator(
            "cinder_service")

        fake_service = mock.Mock(binary="cinder_service", state="up")
        clients = self.credentials["openstack"]["admin"].clients()
        clients.cinder().services.list.return_value = [fake_service]
        result = validator.validate(self.config, self.credentials, None, None)
        self.assertIsNone(result)

        fake_service.state = "down"
        result = validator.validate(self.config, self.credentials, None, None)
        self.assertTrue(result)
        self.assertEqual("cinder_service service is not available",
                         result.msg)


@ddt.ddt
class RequiredAPIVersionsValidatorTestCase(test.TestCase):

    def setUp(self):
        super(RequiredAPIVersionsValidatorTestCase, self).setUp()
        self.config = copy.deepcopy(config)
        self.credentials = copy.deepcopy(credentials)

    def _get_keystone_v2_mock_client(self):
        keystone = mock.Mock()
        del keystone.projects
        keystone.tenants = mock.Mock()
        return keystone

    def _get_keystone_v3_mock_client(self):
        keystone = mock.Mock()
        del keystone.tenants
        keystone.projects = mock.Mock()
        return keystone

    @ddt.unpack
    @ddt.data(
        {"versions": [2.0], "err_msg": "Task was designed to be used with"
                                       " keystone V2.0, but V3 is selected."},
        {"versions": [3], "err_msg": "Task was designed to be used with"
                                     " keystone V3, but V2.0 is selected."},
        {"versions": [2.0, 3], "err_msg": None}
    )
    def test_validate_keystone(self, versions, err_msg):
        validator = validators.RequiredAPIVersionsValidator("keystone",
                                                            versions)

        clients = self.credentials["openstack"]["users"][0][
            "credential"].clients()

        clients.keystone.return_value = self._get_keystone_v3_mock_client()
        result = validator.validate(self.config, self.credentials, None, None)

        if result:
            self.assertEqual(err_msg, result.msg)

        clients.keystone.return_value = self._get_keystone_v2_mock_client()
        result = validator.validate(self.config, self.credentials, None, None)

        if result:
            self.assertEqual(err_msg, result.msg)

    @ddt.unpack
    @ddt.data(
        {"nova": 2, "versions": [2], "err_msg": None},
        {"nova": 3, "versions": [2],
         "err_msg": "Task was designed to be used with nova V2, "
                    "but V3 is selected."},
        {"nova": None, "versions": [2],
         "err_msg": "Unable to determine the API version."},
        {"nova": 2, "versions": [2, 3], "err_msg": None},
        {"nova": 4, "versions": [2, 3],
         "err_msg": "Task was designed to be used with nova V2, 3, "
                    "but V4 is selected."}
    )
    def test_validate_nova(self, nova, versions, err_msg):
        validator = validators.RequiredAPIVersionsValidator("nova",
                                                            versions)

        clients = self.credentials["openstack"]["users"][0][
            "credential"].clients()

        clients.nova.choose_version.return_value = nova
        config = {"context": {"api_versions": {}}}

        result = validator.validate(config, self.credentials, None, None)

        if err_msg:
            self.assertIsNotNone(result)
            self.assertEqual(err_msg, result.msg)
        else:
            self.assertIsNone(result)

    @ddt.unpack
    @ddt.data({"version": 2, "err_msg": None},
              {"version": 3, "err_msg": "Task was designed to be used with "
                                        "nova V3, but V2 is selected."})
    def test_validate_context(self, version, err_msg):
        validator = validators.RequiredAPIVersionsValidator("nova",
                                                            [version])

        config = {"context": {"api_versions": {"nova": {"version": 2}}}}

        result = validator.validate(config, self.credentials, None, None)

        if err_msg:
            self.assertIsNotNone(result)
            self.assertEqual(err_msg, result.msg)
        else:
            self.assertIsNone(result)


@ddt.ddt
class VolumeTypeExistsValidatorTestCase(test.TestCase):

    def setUp(self):
        super(VolumeTypeExistsValidatorTestCase, self).setUp()
        self.validator = validators.VolumeTypeExistsValidator("volume_type",
                                                              True)
        self.config = copy.deepcopy(config)
        self.credentials = copy.deepcopy(credentials)

    @ddt.unpack
    @ddt.data(
        {"param_name": "fake_param", "nullable": True, "err_msg": None},
        {"param_name": "fake_param", "nullable": False,
         "err_msg": "The parameter 'fake_param' is required and should"
                    " not be empty."}
    )
    def test_validator(self, param_name, nullable, err_msg):
        validator = validators.VolumeTypeExistsValidator(param_name,
                                                         nullable)

        clients = self.credentials["openstack"]["users"][0][
            "credential"].clients()

        clients.cinder().volume_types.list.return_value = [mock.MagicMock()]

        result = validator.validate(self.config, self.credentials, None, None)

        if err_msg:
            self.assertEqual(err_msg, result.msg)
        else:
            self.assertIsNone(result, "Unexpected result")

    @ddt.unpack
    @ddt.data(
        {"context": {"args": {"volume_type": "fake_type"}},
         "volume_type": "fake_type"},
        {"context": {"args": {"volume_type": "fake_type"}}, "volume_type": [],
         "err_msg": "Specified volume type fake_type not found for user {}. "
                    "List of available types: [[]]"}
    )
    def test_volume_type_exists(self, context, volume_type, err_msg=None):
        clients = self.credentials["openstack"]["users"][0][
            "credential"].clients()
        clients.cinder().volume_types.list.return_value = [mock.MagicMock()]
        clients.cinder().volume_types.list.return_value[0].name = volume_type
        result = self.validator.validate(context, self.credentials, None, None)

        if err_msg:
            self.assertIsNotNone(result)
            fake_user = self.credentials["openstack"]["users"][0]
            self.assertEqual(err_msg.format(fake_user), result.msg)
        else:
            self.assertIsNone(result)
