# Model Dreamlink WH1080
#!/usr/bin/env python
#
# This is a python port of 
# http://www.sjcnet.id.au/wordpress/wp-content/plugins/download-monitor/download.php?id=5
#
# If running Ubuntu and getting permissions errors
# Follow the advice given here http://ubuntuforums.org/showthread.php?t=901891
#
# I found the GROUPS portion was REQUIRED on one machine, set it to an appropriate group for
# your system 
#
# /etc/udev/rules.d/41-weather-device.rules
#
#
# SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device",SYSFS{idVendor}=="1941" , SYSFS{idProduct}=="8021", MODE="0666", GROUPS="shane"
#
# OTOH my second machine requires these rules:
#SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device",ATTR{idVendor}=="1941" , ATTR{idProduct}=="8021", MODE="0666", OWNER="shane"
#
# Then remove your device from your machine and plug it back in again

# Minimum requirements:
# - libusb >= 1.0
# - pyusb >= 1.0.0
# - python >= 2.6

import usb.core
import time
import struct
import math
import datetime
import pause

VENDOR = 0x1941
PRODUCT = 0x8021
WIND_DIRS = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW',
             'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']
max_rain_jump = 10
previous_rain = 0
# interval for data collection
period=1 # minutes
# Only required for Weather Underground users
wu_upload_file = "/tmp/wu-wupload.htx"


def open_ws():
    '''
    Open a connection to the device, using the PRODUCT and VENDOR information
    @return reference to the device
    '''
    usb_device = usb.core.find(idVendor=VENDOR, idProduct=PRODUCT)

    if usb_device is None:
        raise ValueError('Device not found')

    usb_device.get_active_configuration()

    # If we don't detach the kernel driver we get I/O errors
    if usb_device.is_kernel_driver_active(0):
        usb_device.detach_kernel_driver(0)

    return usb_device


def read_block(device, offset):
    '''
    Read a block of data from the specified device, starting at the given
    offset.
    @Inputs
    device
        - usb_device
    offset
        - int value
    @Return byte array
    '''

    least_significant_bit = offset & 0xFF
    most_significant_bit = offset >> 8 & 0xFF

    # Construct a binary message
    tbuf = struct.pack('BBBBBBBB',
                       0xA1,
                       most_significant_bit,
                       least_significant_bit,
                       32,
                       0xA1,
                       most_significant_bit,
                       least_significant_bit,
                       32)

    timeout = 1000  # Milliseconds
    retval = dev.ctrl_transfer(0x21,  # USB Requesttype
                               0x09,  # USB Request
                               0x200,  # Value
                               0,  # Index
                               tbuf,  # Message
                               timeout)

    return dev.read(0x81, 32, timeout)

#
# Return dew point based on temperature & humidity
#
# http://en.wikipedia.org/wiki/Dew_Point
#


def dew_point(temperature, humidity):
    '''
    Using the supplied temperature and humidity calculate the dew point
    From Wikipedia: The dew point is the temperature at which the water vapor
    in a sample of air at constant barometric pressure condenses into liquid
    water at the same rate at which it evaporates. [1] At temperatures below
    the dew point, water will leave the air. The condensed water is called dew
    when it forms on a solid surface. The condensed water is called either fog
    or a cloud, depending on its altitude, when it forms in the air.
    @Inputs
    temperature
        - float
    humidity
        - float
    @Return dew point
        - float
    '''
    humidity /= 100.0
    gamma = (17.271 * temperature) / (237.7 + temperature) + math.log(humidity)
    return (237.7 * gamma) / (17.271 - gamma)


#
# Return wind chill temp based on temperature & wind speed
#
# http://en.wikipedia.org/wiki/Wind_chill
#

def wind_chill(temperature, wind):
    '''
    Using the supplied temperature and wind speed calculate the wind chill
    factor.
    From Wikipedia: Wind-chill or windchill, (popularly wind chill factor) is
    the perceived decrease in air temperature felt by the body on exposed skin
    due to the flow of air
    '''
    wind_kph = 3.6 * wind

    # Low wind speed, or high temperature, negates any perceived wind chill
    if ((wind_kph <= 4.8) or (temperature > 10.0)):
        return temperature

    wct = 13.12 + (0.6215 * temperature) - \
        (11.37 * (wind_kph ** 0.16)) + \
        (0.3965 * temperature * (wind_kph ** 0.16))

    # Return the lower of temperature or wind chill temperature
    if (wct < temperature):
        return wct
    else:
        return temperature



############ Open up a connection to the device
dev = open_ws()
dev.set_configuration()


# Program starts the next round minute from now
start_time=datetime.datetime.now()+datetime.timedelta(minutes=1)
start_time=start_time.replace(second=0, microsecond=0)
sampling_time=start_time
print('Program started at ' + str(start_time))
pause.until(start_time)


try:
    while True:
        sampling_time = sampling_time + datetime.timedelta(minutes=period)
        pause.until(sampling_time)
        # daily filenames
        now = str(datetime.datetime.now())
        filename='/home/pi/Desktop/Data/'+now[0:10]+'-Weather.csv'
        file = open(filename,'a')

########### Read data
        # Get the first 32 Bytes of the fixed
        fixed_block = read_block(dev, 0)

        # Check that we have good data
        if (fixed_block[0] != 0x55):
            raise ValueError('Bad data returned')

        # Bytes 31 and 32 when combined create an unsigned short int
        # that tells us where to find the weather data we want
        curpos = struct.unpack('H', fixed_block[30:32])[0]
        current_block = read_block(dev, curpos)

        # Indoor information
        indoor_humidity = current_block[1]
        tlsb = current_block[2]
        tmsb = current_block[3] & 0x7f
        tsign = current_block[3] >> 7
        indoor_temperature = (tmsb * 256 + tlsb) * 0.1
        # Check if temperature is less than zero
        if tsign:
            indoor_temperature *= -1

        # Outdoor information
        outdoor_humidity = current_block[4]
        tlsb = current_block[5]
        tmsb = current_block[6] & 0x7f
        tsign = current_block[6] >> 7
        outdoor_temperature = (tmsb * 256 + tlsb) * 0.1
        # Check if temperature is less than zero
        if tsign:
            outdoor_temperature *= -1

        # Bytes 8 and 9 when combined create an unsigned short int
        # that we multiply by 0.1 to find the absolute pressure
        abs_pressure = struct.unpack('H', current_block[7:9])[0]*0.1
        
        wind = current_block[9]
        gust = current_block[10]
        wind_extra = current_block[11]
        wind_dir = current_block[12]
        
        # Bytes 14 and 15  when combined create an unsigned short int
        # that we multiply by 0.3 to find the total rain
        # I'm not confident that this is correct. Neither abs_pressure nor
        # total_rain are returning sane values. In fact total_rain has
        # stayed static despite rainfall
        # Looks like I fixed it. They used fixed_block instead of current_block
        total_rain = struct.unpack('H', current_block[13:15])[0]*0.3

        # Calculate wind speeds
        wind_speed = (wind + ((wind_extra & 0x0F) << 8)) * 0.38  # Was 0.1
        gust_speed = (gust + ((wind_extra & 0xF0) << 4)) * 0.38  # Was 0.1

        outdoor_dew_point = dew_point(outdoor_temperature, outdoor_humidity)
        wind_chill_temp = wind_chill(outdoor_temperature, wind_speed)

        # Calculate rainfall rates
        if previous_rain == 0:
            previous_rain = total_rain

        rain_diff = total_rain - previous_rain

        if rain_diff > max_rain_jump:  # Filter rainfall spikes
            rain_diff = 0
            total_rain = previous_rain

        previous_rain = total_rain;

############### Save data
        print(str(datetime.datetime.now())+' Saving data')
        file.write(str(datetime.datetime.now()))
        file.write(',')
        file.write('%i,' %indoor_humidity)
        file.write('%i,' %outdoor_humidity)
        file.write('%2.1f,' %indoor_temperature)
        file.write('%2.1f,' %outdoor_temperature)
        file.write('%2.2f,' %outdoor_dew_point)
        file.write('%2.1f,' %wind_chill_temp)
        file.write('%2.1f,' %wind_speed)
        file.write('%2.1f,' %gust_speed)
        file.write('%s,' %WIND_DIRS[wind_dir])
        file.write('%2.1f,' %rain_diff)
        file.write('%3.1f,' %total_rain)
        file.write('%4.1f,' %abs_pressure)
        file.write('\n')
        file.close()

except (KeyboardInterrupt, SystemExit):
    print ('\n...Program Stopped Manually!')
