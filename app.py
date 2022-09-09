from pyexpat.errors import messages
from ctypes.wintypes import tagRECT
from requests import session
from flask import Flask, request, jsonify, render_template, make_response, redirect, url_for
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import re
import pytz
from datetime import datetime
import logging
import os
import time
import json
import psycopg2
import requests
import pandas as pd
from pathlib import Path
from opencensus.ext.azure.trace_exporter import AzureExporter
from opencensus.ext.flask.flask_middleware import FlaskMiddleware
from opencensus.trace.samplers import ProbabilitySampler
import logging
from opencensus.ext.azure.log_exporter import AzureLogHandler
import uuid
from multiprocessing import Process
import hashlib

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# create app and connect logger to azure application insights
app = Flask(__name__, static_url_path='')

limiter = Limiter(
    app,
    key_func=get_remote_address
)

app_insights_connection = os.environ['APPINSIGHTS_INSTRUMENTATIONKEY']
logger.addHandler(AzureLogHandler(
    connection_string=app_insights_connection)
)
middleware = FlaskMiddleware(
    app,
    exporter=AzureExporter(connection_string=app_insights_connection),
    sampler=ProbabilitySampler(rate=1.0),
)

DATABASE_URL = os.environ['DATABASE_URL']
secret = os.environ['APP_SECRET']

if app.debug:
    print("In debuugggging mode")
    # api_url = "https://oeverapi.azurewebsites.net/"
    api_url = "http://127.0.0.1:8000/"
else:
    print("taking online api")
    api_url = "https://oeverapi.azurewebsites.net/"
    # api_url = "http://127.0.0.1:8000/"


tz = pytz.timezone('Europe/Berlin')

def get_sentence_id(sentence, translation, session):
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute("SELECT sentence_id FROM sentences WHERE deu = %s AND nds_ai = %s AND session = %s", (sentence, translation, session))
        sentence_id = cur.fetchone()
        cur.close()
        conn.close()
        return sentence_id[0]
    except Exception as e:
        print(e)
        return None

def feedback_db(sentence_id, option, correction=False):
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        print("new connection to database")
        if correction:
            cur.execute("UPDATE feedback SET correction = %s WHERE Sentence_id = %s", (correction,sentence_id))
        else:
            cur.execute("INSERT INTO feedback (sentence_id,correct) VALUES (%s,%s)", (sentence_id,option))
        conn.commit()
        cur.close()
        conn.close()
    except:
        pass

def get_ID(sessionID):
    if not sessionID:
        new_sessionID =  uuid.uuid4()
        print("no cookie set, setting sessionID to", new_sessionID)
        return str(new_sessionID)
    return sessionID

def get_user_information(request, sessionID):
    user_info = dict()
    # user_info["remote_addr"] = request.remote_addr
    # user_info["accept_mimetypes"] = request.accept_mimetypes
    try:
        user_info["access_route"] = [f.split(":")[0] for f in request.access_route]
    except Exception as e:
        print(e)
    # print(user_info["access_route"])
    # user_info["content_encoding"] = request.content_encoding
    user_info["sessionID"] = sessionID
    user_info["user_agent_string"] = request.user_agent.string
    # print("user agent string", request.user_agent.string)
    # print("created dict", user_info)
    hex = hashlib.sha1(json.dumps(user_info).encode('utf-8')).hexdigest()
    return user_info, hex

def insert_user_information(request, sessionID, referrer=None):
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        print("new connection to database")
        time_now = datetime.now(tz)
        user_info, hex = get_user_information(request, sessionID)
        cur.execute("INSERT INTO session_info (sessionid,user_agent_string,user_ip,user_info_hash,timestamp,referrer) VALUES (%s,%s,%s,%s,%s,%s)", (user_info["sessionID"],user_info['user_agent_string'],user_info["access_route"],hex, time_now, referrer))
        conn.commit()
        cur.close()
        conn.close()
        # time.sleep(10)
        print("entry into database successful", datetime.now())
    except Exception as e:
        logger.error("Entry into database failed", exc_info=e)
        print("entry into database failed", e)    


def get_translation(text, lang, session=None, autocorrected_text=False):
    payload = json.dumps({"sentence": text, "target_language":lang, "session":session})
    header = {'Authorization': f'Bearer {secret}', 'Content-Type': 'application/json'}
    response = requests.post(api_url + "translate/", data=payload, headers=header)
    result = json.loads(response.text)
    return result


@app.route("/")
def home():
    # message = request.form.get('message')
    resp = make_response(render_template("index.html")) # message
    sessionID = request.cookies.get('sessionID') 
    if not sessionID:
        sessionID = get_ID(sessionID)
        resp.set_cookie('sessionID', sessionID)
    start_db = datetime.now()
    print("start running async process", )
    insert_info = Process( 
        target=insert_user_information,
        args=(request,sessionID),
        daemon=True
        )
    insert_info.start()    
    # await insert_user_information(request, sessionID)
    end_db = datetime.now()
    print("after running async process, took secs:", end_db - start_db)
    return resp

@app.route("/impressum")
def impressum():
  return render_template("impressum.html")

@app.route("/datenschutz")
def datenschutz():
  return render_template("privacypolicy.html")

@app.route("/about")
def about():
  return render_template("about.html")

@app.route("/oeversett", methods = ['GET'])
@limiter.limit("10/minute", error_message='Die Anzahl an Anfragen ist begrenzt, da jede Übersetzung berechnet werden muss. Bitte versuche es in Kürze noch einmal.')
def oeversett():
    sentence = request.args.get('q','')    
    sentence = str(sentence).strip()
    lang = request.args.get('lang')
    id = request.args.get("id", None)
    message = ""
    sentence_status = 0
    try:
        try:
            id = int(id)
        except:
            id = 0
        # assert most_common.loc[id,"input"] == sentence
        if id == 89:
            sentence = "Plattdeutsch? Kann ich nun auch!"
        # output_sentence = most_common.loc[id,"output"]
        # sentence_status = 3
        # id_of_new_row = sentence_db(sentence, output_sentence,sentence_status, lang)
        sessionID = request.cookies.get('sessionID') 
        resp = make_response(render_template("index.html", predefined_input=sentence))
        if not sessionID:
            sessionID = get_ID(sessionID)
            resp.set_cookie('sessionID', sessionID)
        insert_info = Process(  # Create a daemonic process with heavy "my_func"
            target=insert_user_information,
            args=(request,sessionID, sentence),
            daemon=True
            )
        insert_info.start()     
        return resp
    except Exception as e:

        sentence_status = 0
        print("Something went wrong catching sentence with id",e)
        logger.error("Something went wrong catching sentence with id", exc_info=e)


@app.route("/evaluation", methods = ['GET'])
def evaluation():
    option = request.args.get("feedback")
    sessionID = request.cookies.get('sessionID') 
    if not option or not sessionID:
        return redirect(url_for('home'))
    sentence = request.args.get("sentence")
    translation = request.args.get("translation")
    print("evaluation", sentence)
    _, session = get_user_information(request,sessionID)
    sentence_id = get_sentence_id(sentence, translation, session)
    print("sentence_id", sentence_id)
    if option == "richtig":
        # insert feedback into 
        feedback_db(sentence_id, 1)
        # return render_template("index.html", message = "Die Übersetzung wurde als korrekt markiert.")
        return render_template("alert.html", message = "Die Übersetzung wurde als korrekt markiert.")

    elif option == "falsch":
        # insert feedback into database and return the input and output sentence to display it on the feedback page
        feedback_db(sentence_id, 0)
        return render_template("feedback.html", feedback = "", sentence_id = sentence_id, nds_ai=str(translation), deu=str(sentence))

    elif option == "alternative":
        # insert feedback into database and return as well input and output sentence to display it on the feedback page
        feedback_db(sentence_id, 2)
        return render_template("feedback.html", feedback = "", sentence_id = sentence_id, nds_ai=str(translation), deu=str(sentence))
                


@app.route("/correction", methods = ['POST', 'GET'])
def correction():
    correction = request.form.get('korrektur')
    if not correction:
        return redirect(url_for('home'))
    sentence_id = request.form.get('sentence_id')
    # write correction into database
    feedback_db(sentence_id, 1, correction)
    # return render_template("index.html", message = "Vielen Dank für die Verbesserung. Der Översetter lernt nicht sofort, sondern die Korrekturen werden überprüft und erst ab einer ausreichenden Anzahl lohnt sich ein erneutes Training.")
    return render_template("alert.html", message = "Vielen Dank für die Verbesserung. Der Översetter lernt nicht sofort, sondern die Korrekturen werden überprüft und erst ab einer ausreichenden Anzahl lohnt sich ein erneutes Training.")


@app.route('/translation')
def suggestions():
    text = request.args.get('jsdata')
    text = text.strip()
    lang = request.args.get('lang')
    sessionID = request.cookies.get('sessionID')
    start_user_info = datetime.now()
    _, session = get_user_information(request,sessionID)
    end_user_info = datetime.now()
    print("hashing session info took", end_user_info - start_user_info)
    print("cookie inside translation route", sessionID)
    start_translation = datetime.now()
    result = get_translation(text, lang, session=session)
    end_translation = datetime.now()
    print("translation took", end_translation - start_translation)
    print(result)
    print("translation input", result["sentence"])
    return render_template('translation.html', translation=result["translation"], sentence = result["sentence"])


@app.errorhandler(500)
def page_not_found(e):
    return render_template('500.html'), 500

if __name__ == "__main__":
    app.run(debug=True)


