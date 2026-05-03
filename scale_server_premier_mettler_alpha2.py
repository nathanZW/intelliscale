from flask import Flask, redirect, url_for, request, render_template, Response
import serial
import serial.tools.list_ports
import requests
import xmlrpc.client
import json

app = Flask(__name__, template_folder='templates')

SERVER_ADDRESS = 'http://192.168.10.2'
DATABASE_NAME = 'ptf_odoo18'
SCALE_ID = "SCALE1"


@app.route('/')
def index():
    return render_template('login.html', login_status='')


@app.route('/getmass/<m>/<b>')
def getmass(m, b):
    mass = ''
    barcode = ''
    if mass:
        mass = m
    if barcode:
        barcode = b
    return render_template('get_mass.html', mass=mass, barcode=barcode)


def scale_connect():
    scale_connected = False
    for comport in serial.tools.list_ports.comports():
        if comport.device.find('ttyUSB') != -1:
            while not scale_connected:
                try:
                    ser = serial.Serial(comport.device)
                    print(ser)
                    scale_connected = True
                except:
                    continue

        elif comport.device.find('serial') != -1 or comport.device.find('COM') != -1:
            while not scale_connected:
                try:
                    ser = serial.Serial(comport.device)
                    print(ser)
                    scale_connected = True
                except:
                    continue

    scale_string = ser.readline()
    print("Scale String:", scale_string)
    mass = scale_string.decode('utf-8')[7: 14].strip()
    
    try:
        ser.close()
    except:
        print("Failed to close Serial Port Connection")
        
    return mass


@app.route('/success/<name>')
def success(name):
    return 'welcome %s' % name


@app.route('/login', methods=['POST', 'GET'])
def login():
    common = xmlrpc.client.ServerProxy(SERVER_ADDRESS + '/xmlrpc/2/common')
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
    uid = common.authenticate(DATABASE_NAME, username, password, {})
    print('Odoo UID: ', uid)
    if uid > 1:
        return render_template('get_mass.html')
    else:
        return render_template('login.html', login_status='Failed to Login to Odoo')


@app.route('/getbarcode', methods=['POST', 'GET'])
def get_barcode():
    if request.method == 'POST':
        mass = scale_connect()
        # mass = '100'
        return render_template('get_barcode.html', mass=mass)
    else:
        return redirect(url_for('save_mass'))


@app.route('/save_mass', methods=['POST', 'GET'])
def save_mass():
    save_status = ''
    mass = 0
    barcode = ''
    if request.method == 'POST':
        mass = round(float(request.form['mass']))
        barcode = request.form['barcode']
        grower_number = request.form.get('grower_number', '0')
        if not grower_number:
            grower_number = '0'
        if mass > 10:
            r = requests.post(SERVER_ADDRESS + '/receiving/scaleserver/manual_scale/' + str(mass) + '/' + barcode + '/' + SCALE_ID + '/' + grower_number,
                              json={"barcode": barcode, "mass": mass, "scale_id": SCALE_ID, "grower_number": grower_number},
                              timeout=10)
            # print('Odoo Response:', r)
            if r.status_code == 200:
                # save_status = "Mass Saved"
                print(r.text)
                save_status = json.loads(r.text)['result']
                save_status = json.loads(save_status)['message']
                print(save_status)
            else:
                save_status = "Error Saving Mass"
                print(r.text)
        else:
            save_status = "Invalid Mass"
        return render_template('get_mass.html', mass=mass, barcode=barcode, save_status=save_status)
    else:
        return render_template('get_mass.html', mass=mass, barcode=barcode)


if __name__ == '__main__':
    app.run(debug=True)
