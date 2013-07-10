import os
import string
import logging
import random
import json
import urllib2
from datetime import datetime, timedelta
import webapp2
import jinja2
from google.appengine.ext import db
from google.appengine.api import memcache
import gcm_api_key

GCM_API_KEY = gcm_api_key.GCM_API_KEY
PASSWORD_LENGTH = 30
ID_LENGTH = 15
jinja_environment = jinja2.Environment(
    loader=jinja2.FileSystemLoader(os.path.dirname(__file__))
)

#===============================================================================

def generate_password():
    return "".join(random.choice(string.letters + string.digits) for i in xrange(PASSWORD_LENGTH))

def generate_id():
    return "".join(random.choice(random.choice("ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789")) for i in xrange(ID_LENGTH)) # except for l I 1 O 0

class Device(db.Model):
    id = db.StringProperty()
    gcm_reg_id = db.StringProperty()
    password = db.StringProperty(indexed=False)
    last_updated = db.DateTimeProperty(indexed=False)
    ip_address = db.StringProperty(indexed=False)
    user_agent = db.StringProperty(indexed=False)
    
    def get_id(self):
        return self.id
    
def get_device(id):
    device = memcache.get(id)
    if not device:
        device = Device.all().filter("id = ", id).get()
        memcache.add(id, device)
    return device

def save_device(device):
    device.put()
    memcache.set(device.get_id(), device)

def delete_device(device):
    if device.get_id():
        memcache.delete(device.get_id())
    db.delete(device)

#===============================================================================

def gcm_send(device, command):
    json_data = {"data" : {"command": command}, "registration_ids": [device.gcm_reg_id]} # "collapse_key" : "main", 
    url = 'https://android.googleapis.com/gcm/send'
    data = json.dumps(json_data)
    headers = {'Content-Type': 'application/json', 'Authorization': "key=" + GCM_API_KEY}
    req = urllib2.Request(url, data, headers)
    f = urllib2.urlopen(req)
    gcm_reply = json.loads(f.read())
    return gcm_reply

#===============================================================================

class MainPage(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/html'
        template = jinja_environment.get_template('templates/index.html')
        self.response.out.write(template.render())

class PrivacyPolicy(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/html'
        template = jinja_environment.get_template('templates/privacy-policy.html')
        self.response.out.write(template.render())
#-------------------------------------------------------------------------------

class Register(webapp2.RequestHandler):
    def post(self):
        gcm_reg_id = self.request.get("gcm_reg_id")
        
        # cleanup old device (in case user reinstalled app and gets the same gcm_reg_id)
        for old_device in Device.all().filter("gcm_reg_id =", gcm_reg_id):
            delete_device(old_device)
        
        device = Device()
        device.id = generate_id()
        device.gcm_reg_id = gcm_reg_id
        device.password = generate_password()
        device.last_updated = datetime.utcnow()
        device.ip_address = self.request.remote_addr
        device.user_agent = self.request.headers['User-Agent']
        
        save_device(device)
        self.response.headers['Content-Type'] = 'application/json'
        response = {"token": device.get_id(), "password": device.password}
        self.response.out.write(json.dumps(response))

class Unregister(webapp2.RequestHandler):
    def post(self):
        id = self.request.get("id")
        password = self.request.get("password")
        device = get_device(id)
        if device:
            if device.password == password:
                delete_device(device)
                logging.debug('unregistered')
            else:
                logging.debug('unregister: wrong password')
        else:
            logging.debug('unregister: wrong device')
        self.response.headers['Content-Type'] = 'text/plain'
        self.response.out.write("")

class Update(webapp2.RequestHandler):
    def post(self):
        self.response.headers['Content-Type'] = 'text/plain'
        id = self.request.get("id")
        password = self.request.get("password")
        device = get_device(id)
        if device and device.password == password:
            try:
                memcache.set("%s:data" % device.get_id(), value={
                    "latitude": float(self.request.get("latitude")),
                    "longitude": float(self.request.get("longitude")),
                    "accuracy": float(self.request.get("accuracy")),
                    "time": int(self.request.get("time"))
                }, time=10)
            except ValueError as e:
                self.response.out.write("unexpected error: wrong input format")
                return
        else:
            self.response.out.write("unexpected error: device not registered")
        self.response.out.write("")

class Ping(webapp2.RequestHandler):
    def post(self):
        self.response.headers['Content-Type'] = 'text/plain'
        id = self.request.get("id")
        password = self.request.get("password")
        device = get_device(id)
        if device and device.password == password:
            device.last_updated = datetime.utcnow()
            save_device(device)
        self.response.out.write("")

#-------------------------------------------------------------------------------

class Wake(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/html'
        response = {"error": ""}
        id = self.request.get("id")
        device = get_device(id)
        if device:
            gcm_reply = gcm_send(device, "wake")
            if gcm_reply.get('failure'):
                reason = gcm_reply["results"][0]["error"] # "InvalidRegistration", "NotRegistered"
                if reason == "NotRegistered": # app uninstalled
                    delete_device(device)
                    response['error'] = "The device is no longer registered because the app was uninstalled."
        else:
            response['error'] = 'device not found'
        self.response.headers['Content-Type'] = 'application/json'
        self.response.out.write(json.dumps(response))


class View(webapp2.RequestHandler):
    def get(self, id):
        self.response.headers['Content-Type'] = 'text/html'
        device = get_device(id)
        if device:
            template = jinja_environment.get_template('templates/view_start.html')
            self.response.out.write(template.render({"device_id": device.get_id()}))
        else:
            self.response.out.write("error: device not found")
    
    def post(self, id):
        self.response.headers['Content-Type'] = 'text/html'
        device = get_device(id)
        if device:
            memcache.delete("%s:data" % device.get_id())
            template = jinja_environment.get_template('templates/view.html')
            self.response.out.write(template.render({"device_id": device.get_id()}))
        else:
            self.response.out.write("error: device not found")

class Get(webapp2.RequestHandler):
    def get(self):
        response = {}
        id = self.request.get("id")
        device = get_device(id)
        if device:
            data = memcache.get("%s:data" % device.get_id()) or {}
            response = {
                "latitude": data.get("latitude"),
                "longitude": data.get("longitude"),
                "accuracy": data.get("accuracy"),
                "time": datetime.fromtimestamp(int(data.get("time") / 1000)).strftime('%Y-%m-%d %H:%M:%S') if data.get("time") else None,
            }
        else:
            response = {"error": "unexpected error: device not registered"}
        self.response.headers['Content-Type'] = 'application/json'
        self.response.out.write(json.dumps(response))


logging.getLogger().setLevel(logging.DEBUG)

app = webapp2.WSGIApplication([
  (r'/', MainPage),
  (r'/privacy-policy', PrivacyPolicy),
  (r'/view/([0-9a-zA-Z-]+)', View),
  (r'/wake', Wake),
  (r'/get', Get),
  
  (r'/register', Register),
  (r'/unregister', Unregister),
  (r'/update', Update),
  (r'/ping', Ping),
  
  
]) # , debug=os.environ.get('SERVER_SOFTWARE', '').startswith('Development')

