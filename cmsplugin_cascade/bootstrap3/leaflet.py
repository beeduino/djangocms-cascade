# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import json

from django.forms.fields import CharField, Field
from django.forms.models import ModelForm, ModelChoiceField
from django.forms import widgets
from django.db.models.fields.related import ManyToOneRel
from django.contrib.admin import StackedInline
from django.contrib.admin.sites import site
from django.core.exceptions import ValidationError
from django.utils.html import format_html
from django.utils.text import unescape_entities
from django.utils.translation import ungettext_lazy, ugettext_lazy as _
from django.utils import six

from filer.fields.image import AdminFileWidget, FilerImageField
from filer.models.imagemodels import Image

from cms.plugin_pool import plugin_pool
from cms.utils.compat.dj import is_installed

from cmsplugin_cascade.fields import GlossaryField
from cmsplugin_cascade.models import InlineCascadeElement
from cmsplugin_cascade.mixins import ImagePropertyMixin
from cmsplugin_cascade.plugin_base import create_proxy_model, CascadePluginBase
from cmsplugin_cascade.settings import CMSPLUGIN_CASCADE
from cmsplugin_cascade.widgets import CascadingSizeWidget


class MarkerForm(ModelForm):
    image_file = ModelChoiceField(
        queryset=Image.objects.all(),
        label=_("Image"),
        required=False,
    )

    image_title = CharField(
        label=_("Image Title"),
        required=False,
        widget=widgets.TextInput(attrs={'size': 60}),
        help_text=_("Caption text added to the 'title' attribute of the <img> element."),
    )

    alt_tag = CharField(
        label=_("Alternative Description"),
        required=False,
        widget=widgets.TextInput(attrs={'size': 60}),
        help_text=_("Textual description of the image added to the 'alt' tag of the <img> element."),
    )

    glossary_field_order = ['image_title', 'alt_tag']

    class Meta:
        exclude = ['glossary']

    def __init__(self, *args, **kwargs):
        try:
            initial = dict(kwargs['instance'].glossary)
        except (KeyError, AttributeError):
            initial = {}
        initial.update(kwargs.pop('initial', {}))
        for key in self.glossary_field_order:
            self.base_fields[key].initial = initial.get(key)
        try:
            self.base_fields['image_file'].initial = initial['image']['pk']
        except KeyError:
            self.base_fields['image_file'].initial = None
        self.base_fields['image_file'].widget = AdminFileWidget(ManyToOneRel(FilerImageField, Image, 'file_ptr'), site)
        if not is_installed('adminsortable2'):
            self.base_fields['order'].widget = widgets.HiddenInput()
            self.base_fields['order'].initial = 0
        super(MarkerForm, self).__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super(MarkerForm, self).clean()
        if self.is_valid():
            image_file = self.cleaned_data.pop('image_file', None)
            if image_file:
                image_data = {'pk': image_file.pk, 'model': 'filer.Image'}
                self.instance.glossary.update(image=image_data)
            else:
                self.instance.glossary.pop('image', None)
            for key in self.glossary_field_order:
                self.instance.glossary.update({key: cleaned_data.pop(key, '')})
        return cleaned_data


class LeafletPluginInline(StackedInline):
    model = InlineCascadeElement
    form = MarkerForm
    raw_id_fields = ['image_file']
    extra = 1


class LeafletForm(ModelForm):
    DEFAULTS = {
        'lat': 30.0,
        'lng': -40.0,
        'zoom': 3,
    }

    leaflet = Field(widget=widgets.HiddenInput)

    class Meta:
        fields = ['glossary']

    def __init__(self, data=None, *args, **kwargs):
        if 'instance' in kwargs:
            initial = dict(kwargs['instance'].glossary)
            initial['leaflet'] = json.dumps(initial.pop('leaflet', self.DEFAULTS))
        else:
            initial = {'leaflet': json.dumps(self.DEFAULTS)}
        super(LeafletForm, self).__init__(data, initial=initial, *args, **kwargs)

    def clean(self):
        try:
            leaflet = self.cleaned_data['leaflet']
            if isinstance(leaflet, six.string_types):
                self.cleaned_data['glossary'].update(leaflet=json.loads(leaflet))
            elif isinstance(leaflet, dict):
                self.cleaned_data['glossary'].update(leaflet=leaflet)
            else:
                raise ValueError
        except (ValueError, KeyError):
            raise ValidationError("Invalid internal leaflet data. Check your Javascript imports.")

    def clean_glossary(self):
        glossary = self.cleaned_data['glossary']
        msg = _("Please specify a valid width")
        if 'responsive' in glossary['map_shapes']:
            if not glossary['map_width_responsive']:
                raise ValidationError(msg)
        else:
            if not glossary['map_width_fixed']:
                raise ValidationError(msg)
        return glossary


class LeafletPlugin(CascadePluginBase):
    name = _("Map")
    module = 'Bootstrap'
    parent_classes = ['BootstrapColumnPlugin']
    require_parent = True
    allow_children = False
    change_form_template = 'cascade/admin/leaflet_plugin_change_form.html'
    ring_plugin = 'LeafletPlugin'
    admin_preview = False
    render_template = 'cascade/bootstrap3/leaflet.html'
    #html_tag_attributes = {'image_title': 'title', 'alt_tag': 'tag'}
    inlines = (LeafletPluginInline,)
    SHAPE_CHOICES = (('img-responsive', _("Responsive")),)
    glossary_field_order = ('map_shapes', 'map_width_responsive', 'map_width_fixed', 'map_height')
    form = LeafletForm

    map_shapes = GlossaryField(
        widgets.CheckboxSelectMultiple(choices=SHAPE_CHOICES),
        label=_("Map Responsiveness"),
        initial=['responsive'],
    )

    map_width_responsive = GlossaryField(
        CascadingSizeWidget(allowed_units=['%'], required=False),
        label=_("Responsive Map Width"),
        initial='100%',
        help_text=_("Set the map width in percent relative to containing element."),
    )

    map_width_fixed = GlossaryField(
        CascadingSizeWidget(allowed_units=['px'], required=False),
        label=_("Fixed Map Width"),
        help_text=_("Set a fixed map width in pixels."),
    )

    map_height = GlossaryField(
        CascadingSizeWidget(allowed_units=['px', '%'], required=True),
        label=_("Adapt Map Height"),
        initial='400px',
        help_text=_("Set a fixed height in pixels, or percent relative to the map width."),
    )

    class Media:
        css = {'all': ['node_modules/leaflet/dist/leaflet.css', 'cascade/css/admin/leafletplugin.css']}
        js = ['node_modules/leaflet/dist/leaflet.js', 'cascade/js/admin/leafletplugin.js']

    def change_view(self, request, object_id, form_url='', extra_context=None):
        extra_context = dict(extra_context or {}, settings=CMSPLUGIN_CASCADE['leaflet'])
        return super(LeafletPlugin, self).change_view(request, object_id, form_url, extra_context)

    def render(self, context, instance, placeholder):
        markers = []
        options = dict(instance.get_complete_glossary())
        for inline_element in instance.sortinline_elements.all():
            # since inline_element requires the property `image`, add ImagePropertyMixin
            # to its class during runtime
            try:
                ProxyModel = create_proxy_model('GalleryImage', (ImagePropertyMixin,),
                                                LeafletPluginInline, module=__name__)
                inline_element.__class__ = ProxyModel
                #options.update(inline_element.glossary, **{
                #    'image_width_fixed': options['thumbnail_width'],
                #    'image_height': options['thumbnail_height'],
                #    'is_responsive': False,
                #})
            except (KeyError, AttributeError):
                pass
        inline_styles = instance.glossary.get('inline_styles', {})
        #inline_styles.update(width=options['thumbnail_width'])
        #instance.glossary['inline_styles'] = inline_styles
        context.update(dict(instance=instance, placeholder=placeholder, settings=CMSPLUGIN_CASCADE['leaflet'],
                            markers=markers))
        return context

    @classmethod
    def get_css_classes(cls, obj):
        css_classes = super(LeafletPlugin, cls).get_css_classes(obj)
        css_class = obj.glossary.get('css_class')
        if css_class:
            css_classes.append(css_class)
        return css_classes

    @classmethod
    def get_identifier(cls, obj):
        identifier = super(LeafletPlugin, cls).get_identifier(obj)
        num_elems = obj.sortinline_elements.count()
        content = ungettext_lazy("with {0} image", "with {0} images", num_elems).format(num_elems)
        return format_html('{0}{1}', identifier, content)

plugin_pool.register_plugin(LeafletPlugin)
