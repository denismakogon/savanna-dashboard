# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright (c) 2013 Mirantis Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from horizon.api import nova
from horizon import forms
import logging

from django.utils.translation import ugettext as _

from horizon import exceptions
from horizon import workflows

from savannadashboard.api import client as savannaclient
from savannadashboard.api import helpers
from savannadashboard.utils.workflow_helpers import _create_step_action
from savannadashboard.utils.workflow_helpers import build_control

LOG = logging.getLogger(__name__)


def get_plugin_and_hadoop_version(request):
    plugin_name = request.session.get("plugin_name")
    hadoop_version = request.session.get("hadoop_version")
    return (plugin_name, hadoop_version)


class GeneralConfigAction(workflows.Action):
    nodegroup_name = forms.CharField(label=_("Template Name"),
                                     required=True)

    flavor = forms.ChoiceField(label=_("OpenStack Flavor"),
                               required=True)

    hidden_configure_field = forms.CharField(
        required=False,
        widget=forms.HiddenInput(attrs={"class": "hidden_configure_field"}))

    def __init__(self, request, *args, **kwargs):
        super(GeneralConfigAction, self).__init__(request, *args, **kwargs)

        savanna = savannaclient.Client(request)
        hlps = helpers.Helpers(savanna)

        plugin, hadoop_version = get_plugin_and_hadoop_version(request)

        process_choices = hlps.get_node_processes(plugin, hadoop_version)
        self.fields["processes"] = forms.MultipleChoiceField(
            label=_("Processes"),
            required=False,
            widget=forms.CheckboxSelectMultiple(),
            help_text=_("Processes to be launched in node group"),
            choices=process_choices)

        node_parameters = hlps.get_general_node_group_configs(plugin,
                                                              hadoop_version)

        for param in node_parameters:
            self.fields[param.name] = build_control(param)

    def populate_flavor_choices(self, request, context):
        #todo filter images by tag, taken from context
        try:
            flavors = nova.flavor_list(request)
            flavor_list = [(flavor.id, "%s" % flavor.name)
                           for flavor in flavors]
        except Exception:
            flavor_list = []
            exceptions.handle(request,
                              _('Unable to retrieve instance flavors.'))
        return sorted(flavor_list)

    def get_help_text(self):
        extra = dict()
        extra["plugin_name"] = self.request.session.get("plugin_name")
        extra["hadoop_version"] = self.request.session.get("hadoop_version")
        return super(GeneralConfigAction, self).get_help_text(extra)

    class Meta:
        name = _("Configure Node group Template")
        help_text_template = \
            ("nodegroup_templates/_configure_general_help.html")


class GeneralConfig(workflows.Step):
    action_class = GeneralConfigAction

    def contribute(self, data, context):
        for k, v in data.items():
            if "hidden" in k:
                continue
            context["general_" + k] = v
        return context


class ConfigureNodegroupTemplate(workflows.Workflow):
    slug = "configure_nodegroup_template"
    name = _("Create Node group Template")
    finalize_button_name = _("Create")
    success_message = _("Created")
    failure_message = _("Could not create")
    success_url = "horizon:savanna:nodegroup_templates:index"
    default_steps = (GeneralConfig,)

    def __init__(self, request, context_seed, entry_point, *args, **kwargs):
        #todo manage registry cleanup
        ConfigureNodegroupTemplate._cls_registry = set([])

        savanna = savannaclient.Client(request)
        hlps = helpers.Helpers(savanna)

        plugin, hadoop_version = get_plugin_and_hadoop_version(request)

        process_parameters = hlps.get_targeted_node_group_configs(
            plugin,
            hadoop_version)

        for process, parameters in process_parameters.items():
            step = _create_step_action(process,
                                       title=process + " parameters",
                                       parameters=parameters)
            ConfigureNodegroupTemplate.register(step)

        super(ConfigureNodegroupTemplate, self).__init__(request,
                                                         context_seed,
                                                         entry_point,
                                                         *args, **kwargs)

    def is_valid(self):
        missing = self.depends_on - set(self.context.keys())
        if missing:
            raise exceptions.WorkflowValidationError(
                "Unable to complete the workflow. The values %s are "
                "required but not present." % ", ".join(missing))
        checked_steps = []
        if "general_processes" in self.context:
            checked_steps = self.context["general_processes"]
        LOG.info(str(checked_steps))

        steps_valid = True
        for step in self.steps:
            if getattr(step, "process_name", None) not in checked_steps:
                LOG.warning(getattr(step, "process_name", None))
                continue
            if not step.action.is_valid():
                steps_valid = False
                step.has_errors = True
        if not steps_valid:
            return steps_valid
        return self.validate(self.context)

    def handle(self, request, context):
        try:
            LOG.info("create with context:" + str(context))
            return True
        except Exception:
            exceptions.handle(request)
            return False


class SelectPluginAction(workflows.Action):
    hidden_create_field = forms.CharField(
        required=False,
        widget=forms.HiddenInput(attrs={"class": "hidden_create_field"}))

    def __init__(self, request, *args, **kwargs):
        super(SelectPluginAction, self).__init__(request, *args, **kwargs)

        savanna = savannaclient.Client(request)

        plugins = savanna.plugins.list()
        plugin_choices = [(plugin.name, plugin.title) for plugin in plugins]

        self.fields["plugin_name"] = forms.ChoiceField(
            label=_("Plugin name"),
            required=True,
            choices=plugin_choices,
            widget=forms.Select(attrs={"class": "plugin_name_choice"}))

        for plugin in plugins:
            field_name = plugin.name + "_version"
            choice_field = forms.ChoiceField(
                label=_("Hadoop version"),
                required=True,
                choices=[(version, version) for version in plugin.versions],
                widget=forms.Select(
                    attrs={"class": "plugin_version_choice "
                                    + field_name + "_choice"})
            )
            self.fields[field_name] = choice_field

    class Meta:
        name = _("Select plugin and hadoop version")
        help_text_template = ("nodegroup_templates/_create_general_help.html")


class SelectPlugin(workflows.Step):
    action_class = SelectPluginAction
    contributes = ("plugin_name", "hadoop_version")

    def contribute(self, data, context):
        context = super(SelectPlugin, self).contribute(data, context)
        context["plugin_name"] = data.get('plugin_name', None)
        context["hadoop_version"] = \
            data.get(context["plugin_name"] + "_version", None)
        return context


class CreateNodegroupTemplate(workflows.Workflow):
    slug = "create_nodegroup_template"
    name = _("Create Node group Template")
    finalize_button_name = _("Create")
    success_message = _("Created")
    failure_message = _("Could not create")
    success_url = "horizon:savanna:nodegroup_templates:index"
    default_steps = (SelectPlugin,)

    def handle(self, request, context):
        try:
            request.session["plugin_name"] = context["plugin_name"]
            request.session["hadoop_version"] = context["hadoop_version"]
            return True
        except Exception:
            exceptions.handle(request)
            return False