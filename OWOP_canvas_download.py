# Our World of Pixels canvas downloader.
# The main logic was copied from owop-renderer.
# https://github.com/OptoCloud/owop-renderer

import websocket
from websocket import ABNF
import sys
import asyncio
import time
import traceback
import threading
import math
from PIL import Image
import os

# Default chunk size
CHUNK_SIZE = 16
ws: websocket.WebSocketApp = None

# Global variables
canvas_name = None

pixel_root_x = None
pixel_root_y = None

chunk_root_x = None
chunk_root_y = None

image_chunks_h = None
image_chunks_v = None

image_chunks_total = None
read_chunks = 0

bmap = None

connected = False
world_connected = False

receive_thread = None

image: Image = None
image_width = None
image_height = None

def connect_to_ws():
    global ws, connected, receive_thread
    print(f'Connecting to {canvas_name}')
    ws = websocket.WebSocketApp(
        f'wss://ourworldofpixels.com/{canvas_name}', 
        on_data=receive_updates,
        on_close=on_close,
        on_error=on_error)
    connected = True
    print(f'Websocket connected.')
    # Maybe not the best implementation, but I hate async
    receive_thread = threading.Thread(target=ws.run_forever, name='Receive thread')
    receive_thread.start()


def on_error(ws, error):
    global connected, world_connected
    connected = False
    world_connected = False
    print('Error communicating with websocket')
    print(error)
    if not all_chunks_drawn():
        connect_to_ws()

def on_close(ws, close_status_code, close_msg):
    global connected, world_connected
    connected = False
    world_connected = False
    print('Websocket closed')
    if not all_chunks_drawn():
        print(f'{close_status_code=}')
        print(f'{close_msg=}')
        connect_to_ws()

def all_chunks_drawn() -> bool:
    return not True in bmap

def should_draw_chunk(cx, cy):
    return bmap[(image_chunks_v * cy) + cx]

def set_chunk_drawn(cx, cy):
    bmap[image_chunks_v * cy + cx] = False

async def request_chunk(x, y):
    try:
        ws.send(int.to_bytes(x, 4, 'little', signed=True) + 
                    int.to_bytes(y, 4, 'little', signed=True), ABNF.OPCODE_BINARY)
    except:
        print('\nError sending chunk request')


# Request chunks
async def request_chunks():
    print('Requesting chunks')
    while not all_chunks_drawn():
        cy = 0
        while cy < image_chunks_h: 
            cx = 0
            while cx < image_chunks_v:
                if should_draw_chunk(cx, cy):
                    while not connected or not world_connected:
                        time.sleep(1)
                    try:
                        await request_chunk(chunk_root_x + cx, chunk_root_y + cy)
                    except:
                        print(traceback.format_exc())
                        cx -= 1
                    time.sleep(1/1000)
                cx += 1
            cy += 1
        time.sleep(0.5)

# Sets a pixel on the image
def set_pixel(r, g, b, x, y):
    global image
    if x < 0 or y < 0 or x >= image_width or y >= image_height:
        return
    image.putpixel((x, y), (r, g, b))

# Prints progress without spamming the console with newlines
def print_progress(value):
    print('\r' + ' ' * 100, end='')  # Clear the current line
    print(f'\rLoaded {read_chunks} of {image_chunks_total}. Canvas is {value * 100:.2f}% complete', end='')

# Reads a chunk, decopresses it and draws pixels on the image
def receive_chunk(data):
    global read_chunks
    cx = int.from_bytes(data[1:5], 'little', signed=True)
    cy = int.from_bytes(data[5:9], 'little', signed=True)
    try:
        if should_draw_chunk(cx - chunk_root_x, cy-chunk_root_y):
            chunk_data = decompress_chunk(data[10:])
            cpx = (cx * CHUNK_SIZE) - pixel_root_x
            cpy = (cy * CHUNK_SIZE) - pixel_root_y

            idx = 0
            for py in range(CHUNK_SIZE):
                for px in range(CHUNK_SIZE):
                    r = chunk_data[idx]
                    g = chunk_data[idx + 1]
                    b = chunk_data[idx + 2]
                    idx += 3
                    set_pixel(r, g, b, cpx + px, cpy + py)

            set_chunk_drawn(cx - chunk_root_x, cy - chunk_root_y)
            read_chunks += 1
            print_progress(read_chunks/float(image_chunks_total))

    except:
        print(traceback.format_exc())

# Function that receives updates
def receive_updates(ws, data, opcode, flag):
    global world_connected
    if data == b'\x05\x03': # First packet from websocket
        print('Connecting to world')
        ws.send(canvas_name.encode() + b'\xdd\x63', ABNF.OPCODE_BINARY)
        world_connected = True
        print('World connected')
        return
    if data[0] == 2: # We only intereset
        receive_chunk(data)

# Chunks are saved compressed. So we need to decompress them first.
def decompress_chunk(u8arr):
    originalLength = (u8arr[1] << 8) | u8arr[0]
    u8decompressedarr = bytearray(originalLength)
    numOfRepeats = (u8arr[3] << 8) | u8arr[2]
    offset = numOfRepeats * 2 + 4
    uptr = 0
    cptr = offset
    i = 0
    while i < numOfRepeats:
        currentRepeatLoc = ((u8arr[4 + i * 2 + 1] << 8)
                            | u8arr[4 + i * 2]) + offset
        while cptr < currentRepeatLoc:
            u8decompressedarr[uptr] = u8arr[cptr]
            uptr += 1
            cptr += 1
        repeatedNum = (u8arr[cptr + 1] << 8) | u8arr[cptr]
        repeatedColorR = u8arr[cptr + 2]
        repeatedColorG = u8arr[cptr + 3]
        repeatedColorB = u8arr[cptr + 4]
        cptr += 5
        while repeatedNum > 0:
            u8decompressedarr[uptr] = repeatedColorR
            u8decompressedarr[uptr + 1] = repeatedColorG
            u8decompressedarr[uptr + 2] = repeatedColorB
            uptr += 3
            repeatedNum -= 1
        i += 1
    while cptr < len(u8arr):
        u8decompressedarr[uptr] = u8arr[cptr]
        uptr += 1
        cptr += 1
    return u8decompressedarr

# Function that check coords of the last image downloaded
def recover_progress(folder_path, u, v):
    file_list = os.listdir(folder_path)

    coords = []
    for file_name in file_list:
        if file_name.endswith('.png'):
            x, y = map(int, file_name[:-4].split('_'))
            coords.append((x, y))
    if coords == []:
        return None, None
    x = pixel_root_x
    while x < u:
        y = pixel_root_y
        while y < v:
            if (x, y) in coords:
                y += 4096
                continue
            return (x, y)
        x += 4096

# Main function
async def main():
    global canvas_name, pixel_root_x, pixel_root_y, chunk_root_x, chunk_root_y, image, image_chunks_h, image_chunks_v, bmap, image_chunks_total, image_width, image_height, read_chunks

    if len(sys.argv) != 6: # Check if we have the right amount of arguments
        print(f'Usage: python {sys.argv[0]} <start x> <start y> <end x> <end y> <canvas name>')
        print('This script downloads the area of the canvas specified by <start x> <start y> <end x> <end y> and <canvas name>.')
        print('It can: \n\tWarn the user if the resulting image is too large.\
\n\tAsk the user if they want to split the image into smaller 4k*4k images.\
\n\tAsk the user to merge the images after they have been downloaded.')
        sys.exit()
    
    pixel_root_x = int(sys.argv[1])
    pixel_root_y = int(sys.argv[2])
    u = int(sys.argv[3]) +1
    v = int(sys.argv[4]) +1
    canvas_name = sys.argv[5]

    # Check is coordinates are aligned wrong
    if pixel_root_x > u or pixel_root_y > v:
        print('Bottom right corner coordinates can\'t be less than top left corner')
        sys.exit()

    image_width = u - pixel_root_x
    image_height = v - pixel_root_y

    chunk_root_x = pixel_root_x // CHUNK_SIZE
    chunk_root_y = pixel_root_y // CHUNK_SIZE
    chunk_u = math.ceil(u / CHUNK_SIZE)
    chunk_v = math.ceil(v / CHUNK_SIZE)

    image_chunks_v = chunk_u - chunk_root_x
    image_chunks_h = chunk_v - chunk_root_y

    image_chunks_total = image_chunks_h * image_chunks_v

    # Warn the user if image is too big
    if image_width > 5000 or image_height > 5000:
        print(f'The resulting image is >5000 pixels in one dimension. ({image_width}x{image_height})\n\
This can be time-consuming and require a lot of RAM. Approximate download time: {image_chunks_total/1000} sec.')
        uinput = input('Are you sure you want to create such a large image? [Y/n]\n')

        if uinput.lower() in ['no', 'n']:
            sys.exit()

        split = True
        uinput = input('Should the image be split into smaller 4k*4k images (recommended)? [Y/n]\n')

        if uinput.lower() in ['no', 'n']:
            split = False

    connect_to_ws()
    if ws is None:
        print('Error connecting to websocket.')
        sys.exit()

    fpath = f'{canvas_name}_{pixel_root_x}_{pixel_root_y}'

    if not os.path.exists(fpath):
        os.mkdir(fpath)
    else:
        print('Restoring progress')
        reinit_counters = False
        last_x, last_y = recover_progress(fpath, u, v)
        if last_x is None or last_y is None:
            print('Progress cannot be restored. Do the files exist? Or is the canvas completely downloaded?')
            split = False
        else:
            print(f'Progress restored! Last image was: {last_x, last_y}')
            reinit_counters = True

    # Image is saved in folder
    if split:

        print('Using splitted images')
        orig_u = u
        orig_v = v

        if reinit_counters:
            x = last_x
        else:
            x = pixel_root_x

        while x < orig_u:
            pixel_root_x = x
            if x + 4096 > orig_u:
                u = orig_u
            else:
                u = x + 4096
            if reinit_counters:
                y = last_y
            else:
                y = pixel_root_y

            while y < orig_v: 
                pixel_root_y = y
                if y + 4096 > orig_v:
                    v = orig_v
                else:
                    v = y + 4096

                image_width = u - pixel_root_x
                image_height = v - pixel_root_y

                chunk_root_x = pixel_root_x // CHUNK_SIZE
                chunk_root_y = pixel_root_y // CHUNK_SIZE
                chunk_u = math.ceil(u / CHUNK_SIZE)
                chunk_v = math.ceil(v / CHUNK_SIZE)

                image_chunks_v = chunk_u - chunk_root_x
                image_chunks_h = chunk_v - chunk_root_y

                image_chunks_total = image_chunks_h * image_chunks_v

                print(f'\nLoadind area from ({pixel_root_x}_{pixel_root_y}) to ({u}_{v}), w,h = {image_width, image_height}')
                image = Image.new('RGBA', (image_width, image_height))
                bmap = [True] * image_chunks_total
                
                read_chunks = 0
                await request_chunks()
                print(f'\nsaving to: {fpath}/{pixel_root_x}_{pixel_root_y}.png')
                image.save(f'{fpath}/{pixel_root_x}_{pixel_root_y}.png')
                
                y+= 4096

            x += 4096
    else:
        image = Image.new('RGBA', (image_width, image_height))
        bmap = [True] * image_chunks_total



        await request_chunks()
        
        image.save(f'{canvas_name}_{image_width}x{image_height}_{pixel_root_x}_{pixel_root_y}.png')

        # image.show()

        
    ws.close()
    time.sleep(1)
    if split:
        combine = True
        uinput = input('\nDo you want to merge downloaded images? [Y, n]')
        if uinput.lower() in ['no', 'n']:
            combine = False

        if combine:
            image = Image.new('RGB', (orig_u - int(sys.argv[1]), orig_v - int(sys.argv[2])))
            file_list = os.listdir(fpath)
            read_chunks = 0
            image_chunks_total = len(file_list)
            for file_name in file_list:
                if file_name.endswith('.png'):
                    img = Image.open(f'{fpath}/{file_name}')
                    x, y = map(int, file_name[:-4].split('_'))
                    image.paste(img, (x - int(sys.argv[1]), y - int(sys.argv[2])))
                    read_chunks += 1
                    print_progress(read_chunks/float(image_chunks_total))
            image.save(f'{canvas_name}_{orig_u - int(sys.argv[1])}x{orig_v - int(sys.argv[2])}_{int(sys.argv[1])}_{int(sys.argv[2])}.png')

    print('\nDone!')

if __name__ == '__main__':
    asyncio.run(main())
