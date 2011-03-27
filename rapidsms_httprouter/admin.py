from django.conf.urls.defaults import *
from django.contrib import admin
from django.core.urlresolvers import reverse
from django import forms
from django.http import HttpResponseRedirect
from .models import Message
from .router import get_router

class MessageAdmin(admin.ModelAdmin):

    def get_urls(self):
        urls = super(MessageAdmin, self).get_urls()
        console_urls = patterns('', (r'^send/$', self.admin_site.admin_view(self.send), {}, 'rapidsms_httprouter_message_send'))
        return console_urls + urls

    class SendForm(forms.Form):
        sender = forms.CharField(max_length=20)
        text = forms.CharField(max_length=160)

    def send(self, request):
        if request.method == 'POST':
            form = self.SendForm(request.POST)
            if form.is_valid():
                message = get_router().handle_incoming('console',
                                                       form.cleaned_data['sender'],
                                                       form.cleaned_data['text'])

        return HttpResponseRedirect(reverse('admin:rapidsms_httprouter_message_changelist'))

    def changelist_view(self, request, extra_context=None):
        if not extra_context:
            extra_context = dict()
        extra_context['title'] = "Messages"
        
        return super(MessageAdmin, self).changelist_view(request, extra_context)

    def identity(self, obj):
        return "<a href='?connection=%s&q=%s'>%s</a>" % (obj.connection.id, obj.connection.identity, obj.connection.identity)
    identity.short_description = "Phone"
    identity.allow_tags = True

    def backend(self, obj):
        return obj.connection.backend.name
    backend.short_description = "Backend"

    def sms_dir(self, obj):
        return "<div class='" + obj.direction + "'></div>"
    sms_dir.short_description = ""
    sms_dir.allow_tags = True

    list_display = ('sms_dir', 'backend', 'identity', 'text', 'date', 'status')
    list_filter = ('status',)
    list_display_links = ('text',)

    actions = None
    search_fields = ('connection__identity', 'text')

    change_list_template = "router/admin/change_list.html"

    class Media:
        css = {
            "all": ("rapidsms_httprouter/stylesheets/admin.css",)
        }

admin.site.register(Message, MessageAdmin)



