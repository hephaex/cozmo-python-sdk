#!/usr/bin/env python3

# Copyright (c) 2017 Anki, Inc.
#
# Licensed under the Apache License, Version 2.0 (the 'License');
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License in the file LICENSE.txt or at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an 'AS IS' BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

'''Advanced example featuring event handlers and image processing using Pillow

This example demonstrates how Cozmo can do simple color recognition to recognize and turn toward the most noticeable object of a specified color.
The color will be determined by toggling the displayed color on a cube (goes between green, yellow, blue, and red).
The other illumintaed cube toggles the display mode of the TkViewer - switching between the normal camera feed and the pixel-annotated version.
'''

import cozmo,sys,math,asyncio,time
from cozmo.util import degrees, radians, abs_val, distance_mm, speed_mmps
from cozmo.lights import Color, Light
from functools import reduce
try:
	from PIL import Image, ImageDraw, ImageColor
except ImportError:
	sys.exit('Cannot import from PIL: Do `pip3 install --user Pillow` to install')

##############################################################################################################################################################################################
#this class is only used in the apply method of ColorFinder, 
#since the add_polygon_to_image method requires a sequences of points 
#whose values can be accessed by '.x' and '.y' (see cozmo.annotate.add_polygon_to_image)


class Point:
	def __init__(self,x,y):
		self.x = x
		self.y = y

##############################################################################################################################################################################################

red_light = cozmo.lights.red_light
green_light = cozmo.lights.green_light
blue_light = cozmo.lights.blue_light
yellow_light = Light(Color(name='yellow', rgb=(255,255,0)))

#these colors are represented by their approximate ranges. For example, red = (minRed=170, maxRed=255, minGreen=0, maxGreen=70,minBlue=70,maxBlue=255)
color_dict = {
'red' : (170,255,0,70,0,70),
'green' : (0,70,170,255,0,70),
'blue' : (0,70,0,70,170,255),
'white' : (0,255,200,255,200,255),
'black' : (0,30,0,30,0,30),
'yellow' : (170,255,170,255,0,70),
}

#currently, this must be updated whenever you add new colors to self.possible_colors
color_to_light_dict = {
'green' : green_light,
'yellow' : yellow_light,
'blue' : blue_light,
'red' : red_light
}

def color_distance(t1,t2):
	r,g,b = t1
	minR,maxR,minG,maxG,minB,maxB = t2
	rdist_sq = 0
	gdist_sq = 0
	bdist_sq = 0
	if r < minR:
		rdist_sq = (minR-r)**2
	if r > maxR:
		rdist_sq = (maxR-r)**2
	if g < minG:
		gdist_sq = (minG-g)**2
	if g > maxG:
		gdist_sq = (maxG-g)**2
	if b < minB:
		bdist_sq = (minB-b)**2
	if b > maxB:
		bdist_sq = (maxB-b)**2
	return rdist_sq+gdist_sq+bdist_sq

##############################################################################################################################################################################################

#this class first defines the method of annotating the screen with the grid of pixels,
#and contains the flow of the app: defining how the cubes control which color to seek, and how Cozmo should turn towards the color.
#We instantiate a BlobDetector object (defined below) to determine where the colors lie in Cozmo's view every time a EvtNewCameraImage event is triggered (roughly 15 times a second)

class ColorFinder(cozmo.annotate.Annotator):
	def __init__(self,robot: cozmo.robot.Robot):
		self.robot = robot

		robot.camera.image_stream_enabled = True
		robot.camera.color_image_enabled = True

		robot.add_event_handler(cozmo.objects.EvtObjectTapped, self.on_cube_tap)
		robot.add_event_handler(cozmo.world.EvtNewCameraImage, self.on_new_camera_image)

		self.color_selector_cube = None
		self.color_to_find = 'green'
		self.color_to_find_index = 0
		self.possible_colors = ['green','yellow','blue','red']

		self.grid_cube = None 
		robot.world.image_annotator.add_annotator('color_finder',self)
		robot.world.image_annotator.annotation_enabled = False

		self.enabled = True #not used, it's just a required field since ColorFinder is a subclass of Annotator

		#these dimensions can be reset - higher numbers means a more accurate approximation (smaller pixels), and a slower processing & reaction time
		self.grid_width = 32
		self.grid_height = 24

		self.pixel_matrix = [['white' for x in range(self.grid_height)] for y in range(self.grid_width)]
		self.fov_x = self.robot.camera.config.fov_x.radians
		self.fov_y = self.robot.camera.config.fov_y.radians
		# 'fov' stands for field of view. this is the width of how wide of an angle cozmo can see to the left or right

		self.amount_turned_recently = radians(0)
		self.moving_threshold = radians(4)

		self.state = 'lookaround'

	def apply(self, image, scale):
		d = ImageDraw.Draw(image)
		WM = 20*32/self.grid_width #width multiplier
		HM = 20*24/self.grid_height #height multiplier
		#these multipliers, when you change self.grid_width and self.grid_height, scale the size of the pixels displayed by the annotator

		#draws a grid of squares on the screen, each of which is filled with the color approximated by the determine_color method
		for i in range(self.grid_width):
			for j in range(self.grid_height):
				pt1 = Point(i*WM,j*HM)
				pt2 = Point(i*WM,(j+1)*HM)
				pt3 = Point((i+1)*WM,(j+1)*HM)
				pt4 = Point((i+1)*WM,j*HM)
				points_seq=(pt1,pt2,pt3,pt4)
				cozmo.annotate.add_polygon_to_image(image,points_seq,1.0,'green',self.pixel_matrix[i][j])

		#displays text in the bottom right corner of the screen telling us which color Cozmo is looking for
		text = cozmo.annotate.ImageText('Looking for {}'.format(self.color_to_find), color='white')
		text.render(d, (0, 0, image.width, image.height))

	#if you tap the color selector cube (displays a color), you switch the color Cozmo is looking for
	#if you tap the grid cube (either blinks white or displays a single white light), you either activate or disactivate the pixel grid annotation in the TkViewer
	def on_cube_tap(self, evt, **kwargs):
		if kwargs['obj'].object_id == self.color_selector_cube.object_id:
			self.toggle_color_to_find()
		elif kwargs['obj'].object_id == self.grid_cube.object_id:
			self.robot.world.image_annotator.annotation_enabled = not self.robot.world.image_annotator.annotation_enabled

	#sets self.color_to_find to the next color in self.possible_colors, wrapping back around to the initial color if we have reached the end of the list
	def toggle_color_to_find(self):
		self.color_to_find_index+=1
		if self.color_to_find_index == len(self.possible_colors):
			self.color_to_find_index = 0
		self.color_to_find = self.possible_colors[self.color_to_find_index]
		self.color_selector_cube.set_lights(color_to_light_dict[self.color_to_find])

	#continuously calls the look_at_it method if there is a blob of color that matches the color Cozmo is looking for
	async def on_new_camera_image(self, evt, **kwargs):
		downsized_image = self.get_low_res_view()
		self.update_pixel_matrix(downsized_image)
		blob_detector = BlobDetector(self.pixel_matrix,self.possible_colors)
		blob_info = blob_detector.get_epicenter_and_range_of_blob_with_color(self.color_to_find)
		if blob_info:
			if self.state=='lookaround':
				self.state='foundcolor'
			self.on_finding_a_blob(blob_info)
		else:
			self.robot.set_backpack_lights_off()
			self.state='lookaround'

	#uses the resize method from the Pillow library to get a low-resolution version of Cozmo's camera view, using the LANCZOS algorithm for approximating the new pixel colors
	def get_low_res_view(self):
		image = self.robot.world.latest_image.raw_image
		return image.resize((self.grid_width,self.grid_height), resample = Image.LANCZOS)

	#uses the getpixel method from the Pillow library to get the RGB values of all the pixels in the low-res approximation of Cozmo's camera view
	def update_pixel_matrix(self,downsized_image):
		for i in range(self.grid_width):
			for j in range(self.grid_height):
				r,g,b = downsized_image.getpixel((i,j))
				self.pixel_matrix[i][j] = self.determine_color(r,g,b)

	#picks the best color to approximate this pixel to, using the method and color values from above
	def determine_color(self,r,g,b):
		min_distance = sys.maxsize
		closest_color = ''
		for color_name, color_values in color_dict.items():
			d = color_distance((r,g,b),color_values)
			if d < min_distance:
				min_distance = d
				closest_color = color_name
		return closest_color

	#called whenever Cozmo finds a blob of the color he is looking for
	def on_finding_a_blob(self,blob_info):
		x,y,min_x,max_x,min_y,max_y = blob_info
		WM = 10*32/self.grid_width # Width Multiplier
		HM = 10*24/self.grid_height # Height Multiplier (see explanation in the apply method)
		self.robot.set_center_backpack_lights(color_to_light_dict[self.color_to_find])
		amount_move_head = radians(self.fov_y*(120-y*HM)/240)
		amount_rotate = radians(self.fov_x*(160-x*HM)/320)
		if self.state=='lookaround' or self.state == 'foundcolor':
			self.look_at_it(amount_move_head,amount_rotate)
		else:
			print('still driving :D')

	#moves Cozmo's head and wheels so that the center of the blob he's detected will now be at the center of his camera view
	#robot.abort_all_actions() tells Cozmo to stop any movements he was previously assigned. This makes sure he is only reacting to the current image
	def look_at_it(self,amount_move_head,amount_rotate):
		self.robot.abort_all_actions()
		new_head_angle = self.robot.head_angle + amount_move_head
		tilt = self.robot.set_head_angle(new_head_angle, in_parallel=True)
		rotate = self.robot.turn_in_place(amount_rotate, in_parallel=True)
		if self.state=='foundcolor':
			self.amount_turned_recently += abs_val(amount_move_head) + abs_val(amount_rotate)			

	#turns on the cube which will toggle the color Cozmo should look for
	def turn_on_color_selector_cube(self):
		paper_clip_cube_type = self.robot.world.light_cubes.get(cozmo.objects.LightCube1Id).object_id
		self.color_selector_cube = self.robot.world._objects[paper_clip_cube_type]
		self.color_selector_cube.set_lights(color_to_light_dict[self.color_to_find])

	#sets the grid_cube to blink in a circular fashion when the pixel grid is not activated, and halts the blinking when the pixel grid is activated
	#blinker taken from 02_cube_blinker tutorial
	#CURRENTLY NOT BEING USED
	async def blink_grid_cube(self):
		heart_cube_type = self.robot.world.light_cubes.get(cozmo.objects.LightCube2Id).object_id
		self.grid_cube = self.robot.world._objects[heart_cube_type]
		while True:
			for i in range(4):
				cols = [cozmo.lights.off_light] * 4
				cols[i] = cozmo.lights.white_light
				self.grid_cube.set_light_corners(*cols)
				await asyncio.sleep(0.1)

	#runs indefinitely, the program ends when you hit CTRL+C in Terminal/Command Prompt, or if you close the TkViewer window
	async def run(self):
		self.robot.set_head_angle(radians(0))		
		self.turn_on_color_selector_cube()
		self.grid_cube = self.robot.world.get_light_cube(cozmo.objects.LightCube2Id)
		self.grid_cube.set_lights(cozmo.lights.white_light)
		while True:
			await asyncio.sleep(4)
			if self.state=='foundcolor' and self.amount_turned_recently<self.moving_threshold:
					self.state='driving'
			print('turned recently {} radians'.format(self.amount_turned_recently))
			self.amount_turned_recently = radians(0)
			print("---------------------------------------------------------------------------------")
			print('state: {}{}{}{}{}{}{}{}{}{}'.format(self.state,self.state,self.state,self.state,self.state,self.state,self.state,self.state,self.state,self.state))
			print("---------------------------------------------------------------------------------")

##############################################################################################################################################################################################

#this class is used to interpret the view through Cozmo's camera to decide where the most prominent blobs of color reside in the grid of pixels


class BlobDetector():
	def __init__(self,matrix,keylist):
		self.matrix = matrix
		self.keylist = keylist
		self.num_blobs = 1
		self.blobs_dict = {}
		self.pixel_keys = None
		self.make_blobs_dict()

	def make_blobs_dict(self):
		self.pixel_keys = [[None for x in range(len(self.matrix[0]))] for y in range(len(self.matrix))]
		for i in range(len(self.matrix)):
			for j in range(len(self.matrix[0])):
				if i==0:
					if j==0:
						self.make_upper_right_corner_blob()
					else:
						if self.matrix[i][j]!=self.matrix[i][j-1]:
							self.make_new_blob_at(i,j)
						else:
							self.join_blob_above(i,j)
				elif j==0:
					if self.matrix[i][j]!=self.matrix[i-1][j]:
						self.make_new_blob_at(i,j)
					else:
						self.join_blob_left(i,j)
				else:
					if self.matrix[i][j]!=self.matrix[i-1][j] and self.matrix[i][j]!=self.matrix[i][j-1]:
						self.make_new_blob_at(i,j)
					elif self.matrix[i][j]==self.matrix[i-1][j] and self.matrix[i][j]==self.matrix[i][j-1] and self.pixel_keys[i-1][j]!=self.pixel_keys[i][j-1]:
						self.merge_up_and_left_blobs(i,j)
					else:
						if self.matrix[i][j]==self.matrix[i-1][j]:
							self.join_blob_left(i,j)
						else:
							self.join_blob_above(i,j)
		self.filter_blobs_dict_by_size(20)

	def get_blobs_dict(self):
		return self.blobs_dict

	def make_upper_right_corner_blob(self):
		self.blobs_dict[(1,self.matrix[0][0])] = [(0,0)]
		self.pixel_keys[0][0] = (1,self.matrix[0][0])
		self.num_blobs+=1

	def make_new_blob_at(self,i,j):
		self.blobs_dict[(self.num_blobs,self.matrix[i][j])] = [(i,j)]
		self.pixel_keys[i][j] = (self.num_blobs,self.matrix[i][j])
		self.num_blobs+=1

	def join_blob_above(self,i,j):
		blob = self.pixel_keys[i][j-1]
		self.blobs_dict[blob] = self.blobs_dict[blob] + [(i,j)]
		self.pixel_keys[i][j] = blob

	def join_blob_left(self,i,j):
		blob = self.pixel_keys[i-1][j]
		self.blobs_dict[blob] = self.blobs_dict[blob] + [(i,j)]
		self.pixel_keys[i][j] = blob

	def merge_up_and_left_blobs(self,i,j):
		above_blob_key = self.pixel_keys[i][j-1]
		left_blob_key = self.pixel_keys[i-1][j]
		above_blob_points = self.blobs_dict[above_blob_key]
		left_blob_points = self.blobs_dict[left_blob_key]
		self.blobs_dict[left_blob_key] = left_blob_points + above_blob_points + [(i,j)]
		self.pixel_keys[i][j] = left_blob_key
		for (x,y) in above_blob_points:
			self.pixel_keys[x][y] = left_blob_key
		self.blobs_dict.pop(above_blob_key)

	def filter_blobs_dict_by_size(self,size):
		self.blobs_dict = dict((k, v) for k, v in self.blobs_dict.items() if len(v) >= size)

	def get_largest_blob_with_color(self,color):
		filtered_dict = dict(((n,k),v) for (n,k),v in self.blobs_dict.items() if k == color)
		values = filtered_dict.values()
		if len(values)>0:
			longest_list = reduce(lambda largest, current: largest if (largest > current) else current, values)
			sample_x,sample_y = longest_list[0]
			return self.pixel_keys[sample_x][sample_y]
		else:
			return None

	def get_blob_info(self):
		info = {}
		biggest_blobs = []
		for color in self.keylist:
			biggest_blobs.append(self.get_largest_blob_with_color(color))
		for blob in biggest_blobs:
			if blob:
				num,color = blob
				xs = []
				ys = []
				min_x = sys.maxsize
				min_y = sys.maxsize
				max_x = 0
				max_y = 0
				for (x,y) in self.blobs_dict[blob]:
					xs.append(x)
					ys.append(y)
					if x > max_x:
						max_x = x
					if x < min_x:
						min_x = x
					if y > max_y:
						max_y = y
					if y < min_y:
						min_y = y
				average_x = reduce((lambda a,b : a+b),xs)/len(xs)
				average_y = reduce((lambda a,b : a+b),ys)/len(ys)
				info[color] = (int(average_x),int(average_y),min_x,max_x,min_y,max_y)
		return info

	def get_epicenter_and_range_of_blob_with_color(self,color):
		d = self.get_blob_info()
		if color in d:
			return d[color]
		return None



##############################################################################################################################################################################################

async def cozmo_program(robot: cozmo.robot.Robot):
	color_finder = ColorFinder(robot)
	await color_finder.run()

cozmo.run_program(cozmo_program, use_viewer=True, force_viewer_on_top=True)