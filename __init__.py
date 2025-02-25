# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTIBILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

bl_info = {
    "name": "Grease Pencil QuickTools v3",
    "author": "pongbuster",
    "version": (3, 1, 4),
    "blender": (4, 3, 0),
    "location": "View3D > N sidebar",
    "description": "Adds grease pencil tool shortcuts to N sidebar",
    "warning": "",
    "doc_url": "",
    "category": "Grease Pencil",
}

import bpy
import gpu
import blf
import os
import json
import mathutils
import addon_utils

from bpy.props import FloatVectorProperty
from bpy.props import StringProperty
from bpy.props import IntProperty
from bpy_extras import view3d_utils
from mathutils import Vector
from pathlib import Path
import math

from gpu_extras.presets import draw_circle_2d
from gpu_extras.batch import batch_for_shader
from gpu.types import GPUShader

def s2lin(x): # convert srgb to linear
    a = 0.055
    if x <= 0.04045:
        y = x * (1.0 /12.92)
    else:
        y = pow ( (x + a) * (1.0 / (1 + a)), 2.4)
    return y


def to_hex(c):
    if c < 0.0031308:
        srgb = 0.0 if c < 0.0 else c * 12.92
    else:
        srgb = 1.055 * math.pow(c, 1.0 / 2.4) - 0.055

    return (max(min(int(srgb * 255 + 0.5), 255), 0)) / 255


def to2d(context, pos3d): # helper function to convert 3d point to 2d
    return view3d_utils.location_3d_to_region_2d(context.region, context.space_data.region_3d, pos3d)


def to3d(context, pos2d): # helper function to convert 2d point to 3d
    return view3d_utils.region_2d_to_location_3d(context.region, context.space_data.region_3d, 
          pos2d, (0,0,0))


def cmp(clr1, clr2):
    delta = 0.03
    return abs(clr1[0] - clr2[0]) < delta and abs(clr1[1] - clr2[1]) < delta and abs(clr1[2] - clr2[2]) < delta

def getPixel(X, Y):
    fb = gpu.state.active_framebuffer_get()
    screen_buffer = fb.read_color(X, Y, 1, 1, 3, 0, 'FLOAT')

    rgb_as_list = screen_buffer.to_list()[0]

    R = rgb_as_list[0][0]
    G = rgb_as_list[0][1]
    B = rgb_as_list[0][2]

    return R, G, B

def centerCamera(context):
    # center and offset camera view
    bpy.ops.view3d.view_center_camera()
    r3d = context.space_data.region_3d
    r3d.view_camera_zoom = 14
    r3d.view_camera_offset = [0.05, -0.005]



class quickFrameSelectionOperator(bpy.types.Operator):
    """
Click and drag to view region
Clcik to zoom to mouse position
Shift click to zoom to selected points
    """
    bl_idname = "quicktools.frame_selection"
    bl_label = "Frame Selection"

    _timer = None
    _handle = None
    _counter = 0
    _first = _last = None
    _mousepos = None
    min3d = max3d = None

    def pan(self, context, minx, miny, maxx, maxy):
        area = context.area
        region = area.regions[-1]
        r3d = context.space_data.region_3d
        
        zoom = r3d.view_camera_zoom
        zoom = (1.41421 + zoom / 50.0)
        zoom *= zoom
        zoom = 2.0 / zoom

        region_center = [region.width / 2, region.height / 2]
        
        to_point = ( (maxx - minx) / 2 + minx, 0, (maxy - miny) / 2 + miny )
        
        sc2d = view3d_utils.location_3d_to_region_2d(area.regions[-1], area.spaces[0].region_3d, to_point)

        pan_x = sc2d[0] - region_center[0]
        pan_y = sc2d[1] - region_center[1]
        
        pan_x *= (zoom / (region.width * 2) )
        pan_y *= (zoom / (region.height * 2 ) )
        
        r3d.view_camera_offset[0] += pan_x
        r3d.view_camera_offset[1] += pan_y
    
    def zoom(self, context, minx, miny, maxx, maxy):
        r3d = context.space_data.region_3d
        
        b = to2d( context, (0, 0, miny) )[1]
        t = to2d( context, (0, 0, maxy) )[1]
        l = to2d( context, (minx, 0, 0) )[0]
        r = to2d( context, (maxx, 0, 0) )[0]
        
        if t > context.area.height * 0.9:
            r3d.view_camera_zoom -= 10
            return 0

        if t < context.area.height * 0.8:
            r3d.view_camera_zoom += 10
            return 0
        
        return 1

    def get_minmax(self, context):
        minx = miny = 9999
        maxx = maxy = -9999

        gp = context.active_object

        if not gp: return  minx, miny, maxx, maxy
        if gp.type != 'GREASEPENCIL': return  minx, miny, maxx, maxy
        
        if self._first != None:
            return self.min3d[0], self.min3d[2], self.max3d[0], self.max3d[2]

        for lr in gp.data.layers:
            if lr.lock == True or lr.hide == True: continue
            frame = lr.current_frame()
            if not frame: continue
        
            for stroke in frame.drawing.strokes:
                for point in stroke.points:
                    if point.select:
                        minx = min(minx, point.position[0])
                        maxx = max(maxx, point.position[0])
                        miny = min(miny, point.position[2])
                        maxy = max(maxy, point.position[2])
                        
        return minx, miny, maxx, maxy

    def modal(self, context, event):
        context.area.tag_redraw()
        
        if event.type in {'RIGHTMOUSE', 'ESC'}:
            self.cancel(context)
            return {'CANCELLED'}
        
        if event.shift and self._first == None and not self._timer:
            wm = context.window_manager
            self._timer = wm.event_timer_add(0.001, window=context.window)
            self._last = (event.mouse_region_x, event.mouse_region_y)

        if event.type == 'MOUSEMOVE':
            self._mousepos = (event.mouse_region_x, event.mouse_region_y)
        elif event.type == 'LEFTMOUSE' and not event.shift:
            if self._first == None:
                self._first = (event.mouse_region_x, event.mouse_region_y)
                context.window.cursor_modal_set("PICK_AREA")
                return {'RUNNING_MODAL'}
            else:
                context.window.cursor_modal_restore()
                self._last = (event.mouse_region_x, event.mouse_region_y)
                if not self._timer and self._first:
                    first3d = to3d(context, self._first)
                    last3d = to3d(context, self._last)
                    minx = min(first3d[0], last3d[0])
                    maxx = max(first3d[0], last3d[0])
                    miny = min(first3d[2], last3d[2])
                    maxy = max(first3d[2], last3d[2])
                    self.min3d = (minx, 0, miny)
                    self.max3d = (maxx, 0, maxy)
                    wm = context.window_manager
                    self._timer = wm.event_timer_add(0.001, window=context.window)

        if event.type == 'TIMER' and self._last:
            if self._handle:
                bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
                self._handle = None
            
            minx, miny, maxx, maxy = self.get_minmax(context)
            if minx == 9999: return self.cancel(context)
            
            if self._counter < 20:    
                self.pan(context, minx, miny, maxx, maxy)
            elif self._counter < 40:
                if self.zoom(context, minx, miny, maxx, maxy):
                    return self.cancel(context)
            else:
                return self.cancel(context)
            
            self._counter += 1
            
            return {'PASS_THROUGH'}
        
        return {'RUNNING_MODAL'}

    def draw_callback_px(self, context):
        
        if self._first and self._mousepos:
            lines = []
            lines.append(self._first)
            lines.append( (self._mousepos[0], self._first[1]) )
            lines.append(self._mousepos)
            lines.append( (self._first[0], self._mousepos[1]) )
            lines.append(self._first)
            
            shader = gpu.shader.from_builtin('UNIFORM_COLOR')
            gpu.state.blend_set('ALPHA')
            batch = batch_for_shader(shader, 'LINE_STRIP', {"pos": lines})
            gpu.state.line_width_set(3.0)
            shader.uniform_float("color", (1.0, 1.0, 1.0, 1.0))
            batch.draw(shader)
            gpu.state.line_width_set(2.0)
            shader.uniform_float("color", (0.0, 0.0, 0.0, 1.0))
            batch.draw(shader)

    def execute(self, context):
        if self._timer: wm.event_timer_remove(self._timer)
        self._first = None
        args = (context,)
        self._handle = bpy.types.SpaceView3D.draw_handler_add(self.draw_callback_px, args, 'WINDOW', 'POST_PIXEL')
        wm = context.window_manager
        wm.modal_handler_add(self)
        context.window.cursor_modal_set("ZOOM_IN")

        return {'RUNNING_MODAL'}

    def cancel(self, context):
        self._first = None
        wm = context.window_manager
        if self._timer: 
            wm.event_timer_remove(self._timer)
            self._timer = None
        context.window.cursor_modal_restore()
        if self._handle:
            bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
            self._handle = None
        context.window.cursor_modal_restore()
        return {'FINISHED'}


class quickEyeDropperOperator(bpy.types.Operator):
    """Left click to sample Fill color.
SHIFT-Left click to sample Stroke color.
CTRL-Left click to sample a selected Point's radius
"""

    bl_idname = "quicktools.eyedropper"
    bl_label = "QuickTools Color Eyedropper"
    bl_options = {'REGISTER' }
    
    _selectedPoint = _drawPoint = _handle = None
    
    @classmethod
    def poll(self, context):
        if context.active_object == None or context.active_object.type != 'GREASEPENCIL': return False
        return True
        return (context.mode == 'PAINT_GREASE_PENCIL' or context.mode == 'VERTEX_GREASE_PENCIL')
    
    def draw_callback_px(self, context):
        radius = 10
        col = (1, 0, 0, 1)

        lw = gpu.state.line_width_get()
        gpu.state.line_width_set(2.0)

        if self._selectedPoint:
            draw_circle_2d(self._drawPoint, col, radius)
        
    def modal(self, context, event):
        context.area.tag_redraw()
        
        if self._selectedPoint:
            context.area.header_text_set(f"Radius: {self._selectedPoint.radius}")

        if event.type in {'RIGHTMOUSE', 'ESC'}:
            context.area.header_text_set(None)
            context.workspace.status_text_set("")
            bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
            context.window.cursor_modal_restore()
            self._handle = None
            return {'FINISHED'}
            
        if event.type == "MOUSEMOVE" and event.ctrl:
            gp = context.active_object
            use_multiedit = ume = context.tool_settings.use_grease_pencil_multi_frame_editing        
            strokes = [s for lr in gp.data.layers if not lr.lock and not lr.hide for fr in ([fr for fr in lr.frames if fr.select or fr == lr.current_frame()] if use_multiedit else [lr.current_frame()]) for s in fr.drawing.strokes]
            self._selectedPoint = self._drawPoint = None
            for stroke in strokes:
                if self._selectedPoint:
                    break
                for p in stroke.points:
                    p3d = (p.position[0], p.position[1], p.position[2])
                    p2d = to2d(context, p3d)
                    mouse_pos = (event.mouse_region_x, event.mouse_region_y)
                    delta = Vector(mouse_pos) - Vector(p2d)
                    if delta.length < 10:
                        self._selectedPoint = p
                        self._drawPoint = p2d
                        break
        
        if event.type == "LEFTMOUSE":
            C = bpy.context
            
            if event.ctrl:
                if self._selectedPoint:
                    brush = C.tool_settings.gpencil_paint.brush
                    if brush: 
                        brush.unprojected_radius = self._selectedPoint.radius

                        context.area.header_text_set(None)
                        context.workspace.status_text_set("")
                        bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
                        context.window.cursor_modal_restore()
                        self._handle = None
                        return {'FINISHED'}
                else:
                    return {'RUNNING_MODAL'}

            if context.mode == 'VERTEX_GREASE_PENCIL':
                brush = C.tool_settings.gpencil_vertex_paint.brush
            else:
                brush = C.tool_settings.gpencil_paint.brush
                
            clr = getPixel(event.mouse_x, event.mouse_y)
            
            if brush == None: return {'FINISHED'}

            if event.shift == False:
                brush.gpencil_settings.vertex_mode = 'FILL' 
            else:
                brush.gpencil_settings.vertex_mode = 'STROKE'
                
            brush.color = clr

            context.area.header_text_set(None)
            context.workspace.status_text_set("")
            bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
            context.window.cursor_modal_restore()
            self._handle = None
            return {'FINISHED'}
            
        return {'RUNNING_MODAL'}

    def execute(self, context):
        args = (context,)
        context.workspace.status_text_set("LBUTTON = Fill color, SHIFT+LBUTTON = Stroke Color, CTRL = Sample a selected Point's radius")
        self._handle = bpy.types.SpaceView3D.draw_handler_add(self.draw_callback_px, args, 'WINDOW', 'POST_PIXEL')
        context.window.cursor_modal_set("EYEDROPPER")
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}


class KnifeToolOperator(bpy.types.Operator):
    """Knfe tool inserts points into strokes by slicing across them
    """
    bl_idname = "quicktools.knifetool"
    bl_label = "Knife Tool"

    _start_mode = None
    _handle = None
    
    @classmethod
    def poll(self, context):
        if context.active_object == None or context.active_object.type != 'GREASEPENCIL': return False
        if context.active_object.data.layers.active.lock == True: return False
        try:
            if context.space_data.region_3d.view_perspective != 'CAMERA': return False
        except:
            None
        return (context.mode == 'SCULPT_GREASE_PENCIL' \
            or context.mode == 'EDIT_GREASE_PENCIL')

    def draw_callback_px(self, context):
        
        if self.first:
            lines = []
            lines.append(self.first)
            lines.append(self.mousepos)
            
            # 50% alpha, 2 pixel width line
            shader = gpu.shader.from_builtin('UNIFORM_COLOR')
            gpu.state.blend_set('ALPHA')
            gpu.state.line_width_set(4.0)
            batch = batch_for_shader(shader, 'LINE_STRIP', {"pos": lines})
            shader.uniform_float("color", (0.0, 0.0, 0.0, 0.5))
            batch.draw(shader)

            gpu.state.line_width_set(2.0)
            shader.uniform_float("color", (1.0, 1.0, 1.0, 0.5))
            batch.draw(shader)

            # restore opengl defaults
            gpu.state.line_width_set(1.0)
            gpu.state.blend_set('NONE')


    def modal(self, context, event):
        context.area.tag_redraw()
        
        if event.type in {'RIGHTMOUSE', 'ESC'}:
            context.window.cursor_modal_restore()
            bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
            context.workspace.status_text_set("")
            bpy.ops.object.mode_set(mode=self._start_mode)
            return {'FINISHED'}
            
        elif event.type == 'MOUSEMOVE':
            self.mousepos = (event.mouse_region_x, event.mouse_region_y)
            
            if event.ctrl and self.first:
                lp = Vector(self.first)
                sp = Vector(self.mousepos)
                v = lp - sp
                if v.length > 0:
                    a  = math.degrees(v.angle((1,0)))
                    if abs(a) > 45 and abs(a) < 135:
                        self.mousepos = ( lp[0], sp[1] )
                    else:
                        self.mousepos = ( sp[0], lp[1] )
    

        elif event.type == 'LEFTMOUSE':
            if self.first == None:
                self.first = (event.mouse_region_x, event.mouse_region_y)
                return {'RUNNING_MODAL'}

            self.last = self.mousepos
            
            area = context.area
            space = area.spaces[0]
            
            for region in area.regions:
                if region.type == 'WINDOW':
                    break
                
            gp = context.active_object    
            
            for stroke in gp.data.layers.active.current_frame().drawing.strokes:
                if len(stroke.points) < 2: continue
            
                idx = 0
                cnt = len(stroke.points)
                
                while idx <  cnt:
                    pt1 = stroke.points[idx + 0]
                    pt2 = stroke.points[ (idx + 1) % cnt ]
                    
                    a1 = view3d_utils.region_2d_to_location_3d(context.region, context.space_data.region_3d, 
                        (self.first[0], self.first[1]), (pt1.position))
                    a2 = view3d_utils.region_2d_to_location_3d(context.region, context.space_data.region_3d, 
                        (self.last[0], self.last[1]), (pt1.position))
                        
                    lineA_p1 = Vector((a1[0], a1[2]))
                    lineA_p2 = Vector((a2[0], a2[2]))
                        
                    idx = idx + 1
                    
                    if idx == cnt and not stroke.cyclic:
                        continue

                    lineB_p1 = Vector((pt1.position[0], pt1.position[2]))
                    lineB_p2 = Vector((pt2.position[0], pt2.position[2]))

                    intersect_point = mathutils.geometry.intersect_line_line_2d(lineA_p1, lineA_p2, lineB_p1, lineB_p2)

                    if intersect_point:
                        
                        stroke.add_points(1)
                        cnt = len(stroke.points)
                        idx = idx + 1
                        
                        for pdx in range(cnt - 1, idx - 1, -1):
                            self.copyPoint(stroke.points[pdx - 1], stroke.points[pdx])
                        
                        self.copyPoint(stroke.points[idx - 2], stroke.points[idx -1])    
                        stroke.points[idx - 1].position = Vector((intersect_point[0], 
                            stroke.points[idx - 2].position[1], intersect_point[1]))
                        stroke.points[idx - 1].select = True

            self.first = None
            
        return {'RUNNING_MODAL'}
    

    def copyPoint(self, source_point, dest_point):
        dest_point.select = source_point.select 
        dest_point.delta_time = source_point.delta_time
        dest_point.opacity = source_point.opacity
        dest_point.radius = source_point.radius
        dest_point.rotation = source_point.rotation
        dest_point.vertex_color = source_point.vertex_color
        dest_point.position = source_point.position


    def invoke(self, context, event):
        if context.area.type == 'VIEW_3D' and context.active_object.type == 'GREASEPENCIL':
            # the arguments we pass the the callback
            args = (context,)
            # Add the region OpenGL drawing callback
            # draw in view space with 'POST_VIEW' and 'PRE_VIEW'
            self._handle = bpy.types.SpaceView3D.draw_handler_add(self.draw_callback_px, args, 'WINDOW', 'POST_PIXEL')
            context.window.cursor_modal_set("KNIFE")

            self.first = None
            self.mousepos = None
            self._start_mode = bpy.context.active_object.mode
            bpy.ops.object.mode_set(mode='EDIT')
            bpy.ops.grease_pencil.set_selection_mode(mode='POINT')
            
            context.workspace.status_text_set("Knife Tool: Click and drag cut lines: Right click to finish.")
            
            context.window_manager.modal_handler_add(self)
            return {'RUNNING_MODAL'}
        else:
            self.report({'WARNING'}, "View3D not found, cannot run operator")
            return {'CANCELLED'}
        
        

class quickSnapigonOperator(bpy.types.Operator):
    """Draw polygon with snapping to nearby points of other strokes.
Left click to draw polygon. SPACE/ENTER/MIDDLEMOUSE to add as new stroke.
SHIFT to auto click when snaping to point.
CTRL to restrict to horizontal/vertical lines.
Right click/ESC to finish.

Brush color is used as the FILL color, secondary_color is used as the STROKE color.
"""

    bl_idname = "quicktools.snapigon"
    bl_label = "Snapigon"
    bl_options = {'REGISTER', 'UNDO' }
    
    startend_points = []
    shift_pressed = False
    selectedPoint = None
    _handle = None
  
    @classmethod
    def poll(self, context):
        if context.active_object == None or context.active_object.type != 'GREASEPENCIL': return False
        builtin_brushes = ['brush', 'line', 'box', 'arc', 'curve', 'polyline', 'circle']
        if context.workspace.tools.from_space_view3d_mode(context.mode).idname[8:] not in builtin_brushes:
            return False
        return (context.active_object and context.active_object.type == 'GREASEPENCIL')

    
    def init_startendpoints(self, context): # create array of start and end points of all visible strokes
        gp = context.active_object
        
        self.startend_points.clear()
        
        for lr in gp.data.layers:
            if lr.hide or lr.lock:
                continue
            for fr in lr.frames:
                if fr.frame_number == context.scene.frame_current:
                    for s in fr.drawing.strokes:
                        if len(s.points) > 0:
                            for p in s.points:
                                self.startend_points.append(p)


    def draw_callback_px(self, context):
        radius = 10

        lw = gpu.state.line_width_get()
        gpu.state.line_width_set(2.0)
        
        if len(self.mouse_path) > 0:
            col = (0.1, 1, 0.0, 1)
            draw_circle_2d(to2d(context, self.mouse_path[0]), col, 5)
            
        col = (1, 0, 0, 1)

        if self.selectedPoint:
            draw_circle_2d(self.drawPoint, col, radius)

        gpu.state.line_width_set(lw)
        
        pt = []
        
        if len(self.mouse_path) == 0:
            return
        
        for p in self.mouse_path:
            pt.append(to2d(context, p))
            
        if self.mouse_pos:
            pt.append(self.mouse_pos)
        
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        gpu.state.blend_set('ALPHA')
        batch = batch_for_shader(shader, 'LINE_STRIP', {"pos": pt})
        
        gpu.state.line_width_set(4.0)
        shader.uniform_float("color", (0.0, 0.0, 0.0, 1.0))
        batch.draw(shader)
        
        gpu.state.line_width_set(2.0)
        shader.uniform_float("color", (0.7, 0.7, 0.7, 0.5))
        batch.draw(shader)
        
        for p in self.mouse_path:
            draw_circle_2d(to2d(context, p), (0.3, 0.3, 0.3, 1), 2)

        
    def modal(self, context, event):

        if bpy.context.area is None:
            return {"PASS_THROUGH"}
        
        if event.type == 'MIDDLEMOUSE' and event.shift:
            self.mouse_pos = None
            return {'PASS_THROUGH'}
        
        if event.type == 'WHEELUPMOUSE' or event.type == 'WHEELDOWNMOUSE':
            return {'PASS_THROUGH'}

        if event.type == 'LEFT_SHIFT' or event.type == 'RIGHT_SHIFT':
            self.shift_pressed = event.shift
            
        context.area.tag_redraw()

        self.mouse_pos = (event.mouse_region_x, event.mouse_region_y)

        if event.ctrl and len(self.mouse_path) > 0:
            p2d = to2d(context, self.mouse_path[-1])
            delta = Vector(p2d) - Vector(self.mouse_pos)
            a = delta.angle((1,0)) * 180 / 3.14159 - 90
            if abs(a) > 45:
                self.mouse_pos = (event.mouse_region_x, p2d[1])
            elif abs(a) <= 45:
                self.mouse_pos = (p2d[0], event.mouse_region_y)
            
        if event.type == "MOUSEMOVE":
            self.selectedPoint = None
            
            if len(self.mouse_path) > 2:
                p2d = to2d(context, self.mouse_path[0])
                delta = Vector(p2d) - Vector(self.mouse_pos)
                if delta.length < self.pixels:
                    self.selectedPoint = self.mouse_path[0]
                    self.drawPoint = p2d
                    self.close = True
                else:
                    self.close = False

            if self.close:
                context.window.cursor_modal_set("DOT")
            else:
                for p in self.startend_points:
                    p3d = (p.position[0], p.position[1], p.position[2])
                    p2d = to2d(context, p3d)
                    delta = Vector(self.mouse_pos) - Vector(p2d)
                    
                    if delta.length < self.pixels:
                        self.selectedPoint = p3d
                        self.drawPoint = p2d
                        context.window.cursor_modal_set("PAINT_CROSS")
                        
                        if self.shift_pressed: 
                            if self.mouse_path.count(self.selectedPoint) == 0:
                                self.mouse_path.append(self.selectedPoint)
                        break
                    
                if self.selectedPoint == None:
                    context.window.cursor_modal_set("CROSSHAIR")
                    self.close = False
                    
        if (event.type == 'LEFTMOUSE' and event.value == 'PRESS') or (self.shift_pressed and self.close):
            if self.close:

                if event.ctrl and self.selectedPoint:
                    lp = Vector(self.mouse_path.pop())
                    sp = Vector(self.selectedPoint)
                    v = lp - sp
                    if v.length > 0:
                        a  = math.degrees(v.angle((1,0,0)))
                        if abs(a) > 45 and abs(a) < 135:
                            self.mouse_path.append( [ sp[0], lp[1], lp[2] ] )
                        else:
                            self.mouse_path.append( [ lp[0], lp[1], sp[2] ] )

                self.shift_pressed = False
                self.addStroke(context)
            else:
                if self.selectedPoint:
                    self.mouse_path.append(self.selectedPoint)

                    if event.ctrl and len(self.mouse_path) > 1:
                        lp = Vector(self.mouse_path[-2])
                        sp = Vector(self.selectedPoint)
                        v = lp - sp
                        if v.length > 0:
                            a  = math.degrees(v.angle((1,0,0)))
                            if abs(a) > 45 and abs(a) < 135:
                                self.mouse_path[-2] = [ sp[0], lp[1], lp[2] ]
                            else:
                                self.mouse_path[-2] = [ lp[0], lp[1], sp[2] ]
                else:
                    pos = to3d(context, self.mouse_pos)
                    self.mouse_path.append(pos)
            
        elif event.type in {'SPACE', 'RET', 'NUMPAD_ENTER',  'MIDDLEMOUSE'} and event.value == 'RELEASE':
            self.addStroke(context)        
            return {'RUNNING_MODAL'}

        elif event.type in {'RIGHTMOUSE', 'ESC'}:
            context.window.cursor_modal_restore()
            bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
            context.area.tag_redraw()
            return {'FINISHED'}

        return {'RUNNING_MODAL'}
    
    def addStroke(self, context):
        bpy.ops.ed.undo_push(message = 'Added snapigon')
        
        C = context

        r3d = context.space_data.region_3d
        
        matIndex = C.active_object.active_material_index
        brush = C.tool_settings.gpencil_paint.brush
        radius = brush.unprojected_radius
        
        clr = C.tool_settings.gpencil_paint.brush.secondary_color
        vertexColor = (s2lin(clr.r), s2lin(clr.g), s2lin(clr.b), 1)
        
        clr = C.tool_settings.gpencil_paint.brush.color 
        fillColor = (s2lin(clr.r), s2lin(clr.g), s2lin(clr.b), 1)
        
        gp = C.active_object
        
        frame = gp.data.layers.active.current_frame()
        
        if frame == None:
            try:
                bpy.ops.grease_pencil.insert_blank_frame()
            except:
                self.report({'ERROR'}, message="Error adding keyframe")
                return {'CANCELLED'}
        elif frame.frame_number != context.scene.frame_current:
            bpy.ops.grease_pencil.insert_blank_frame()
        for frame in gp.data.layers.active.frames:
            if frame.frame_number == context.scene.frame_current:
                break
        if frame == None:
            self.report({'ERROR'}, message="Error adding keyframe to layer")
            return {'CANCELLED'}
        
        drawing = frame.drawing

        drawing.add_strokes([len(self.mouse_path)])
        newStroke = drawing.strokes[-1]
        newStroke.material_index = matIndex
        newStroke.fill_color = fillColor
        newStroke.fill_opacity = brush.strength
        
        for idx, pt in enumerate(self.mouse_path):
            newStroke.points[idx].position = pt
            newStroke.points[idx].radius = radius
            newStroke.points[idx].vertex_color = vertexColor
            newStroke.points[idx].opacity = brush.strength                        
        newStroke.cyclic = self.close
        
        # bug in Blender v4.3+ does not initialize fill_opacity = 1 
        for stroke in drawing.strokes: 
            if stroke.fill_opacity == 0: 
                stroke.fill_opacity = 1

        self.init_startendpoints(context)
        self.mouse_path.clear()
        self.selectedPoint = None
        self.drawPoint = None
        self.close = False
        
        
# BUG: drawing.reorder_strokes also not available in 4.3      
#        if context.scene.tool_settings.use_gpencil_draw_onback:
#            order = []
#            for idx in range(len(drawing.strokes)):
#                order.append(idx + 1)
#            order[-1] = 0
#            print(order)
#            drawing.reorder_strokes(order)

        context.area.tag_redraw()
        

    def execute(self, context):
        self.mouse_path = []
        self.mouse_path.clear()
        self.pixels = 10
        self.close = False

        gp = context.active_object
        if gp.data.layers.active.lock == True or gp.data.layers.active.hide == True: 
            self.report({'ERROR'}, message="Active layer is locked or hidden")
            return {'CANCELLED'}    

        self.init_startendpoints(context)

        if context.area.type == 'VIEW_3D':
            self._handle = bpy.types.SpaceView3D.draw_handler_add(self.draw_callback_px, (context,), 'WINDOW', 'POST_PIXEL')                    
            context.window_manager.modal_handler_add(self)
            context.window.cursor_modal_set("CROSSHAIR")
            return {'RUNNING_MODAL'}
        else:
            self.report({'WARNING'}, "View3D not found, cannot run operator")
            return {'CANCELLED'}            
        
        
class quickSubMergeOperator(bpy.types.Operator):
    """
    Adjust point spacing between selected points.
    Scroll mouse wheel to increase / decrease spacing.
    Shift for smaller increments.
    Left click to apply. Right click to cancel.
    """
    
    bl_idname = "quicktools.submerge_strokes"
    bl_label = "SubMerge Stroke"
    bl_options = {'REGISTER', 'UNDO'}

    _submerge_interval = 0.001
    _submerge_spacing = 0.00
    
    _init_select_mode = 0
    _init_sculpt_mask_point = 0
    _init_sculpt_mask_stroke = 0
    _mouse_start = None
    _startend = []
    _lbuttondown = False
    _refresh = False
    _dragging = False

    @classmethod
    def poll(self, context):
        if context.active_object == None or context.active_object.type != 'GREASEPENCIL': return False
        return (context.mode == 'SCULPT_GREASE_PENCIL' or context.mode == 'EDIT_GREASE_PENCIL')
    
    def subdivide_and_merge(self):
        bpy.ops.ed.undo_push(message = "Subdivide and Merge")
    
        idx = 5
        
        while idx > 0:    
            bpy.ops.grease_pencil.stroke_subdivide(number_cuts = 1, only_selected = True)
            bpy.ops.grease_pencil.stroke_merge_by_distance(threshold=0.005)
            idx = idx - 1

        bpy.ops.grease_pencil.stroke_merge_by_distance(threshold=self._submerge_spacing)

        # restore start and end point positions
        idx = 0
        gp = bpy.context.active_object
        for layer in gp.data.layers:
            if layer.hide == True or layer.lock == True: continue
            fr = layer.current_frame()
            if fr == None: continue
            for stroke in fr.drawing.strokes:
                if stroke.select and not stroke.cyclic:
                    pos = self._startend[idx]
                    stroke.points[0].position = pos[0]
                    stroke.points[-1].position = pos[1]
                    idx = idx + 1
                    

    def modal(self, context, event):
        if event.type in {'RIGHTMOUSE', 'ESC'}:
            context.area.header_text_set(None)
            bpy.ops.ed.undo()

            context.window.cursor_modal_restore()
            context.scene.tool_settings.gpencil_selectmode_edit = self._init_select_mode   

            if self._init_sculpt_mask_stroke:
                context.scene.tool_settings.use_gpencil_select_mask_stroke = True
            else:
                context.scene.tool_settings.use_gpencil_select_mask_point = self._init_sculpt_mask_point

            return {'CANCELLED'}

        if event.type == "LEFTMOUSE" and event.value == 'RELEASE' and not event.shift:
            self._lbuttondown = False

            if  not self._dragging:
                context.area.header_text_set(None)
                context.scene['submerge_spacing'] = self._submerge_spacing
                
                context.window.cursor_modal_restore()

                context.scene.tool_settings.gpencil_selectmode_edit = self._init_select_mode
                
                if self._init_sculpt_mask_stroke:
                    context.scene.tool_settings.use_gpencil_select_mask_stroke = True
                else: 
                    context.scene.tool_settings.use_gpencil_select_mask_point = self._init_sculpt_mask_point
                    
                return {'FINISHED'}
                
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            self._mouse_start = (event.mouse_x, event.mouse_y)
            self._lbuttondown = True
            self._dragging = False

        elif event.type == 'WHEELUPMOUSE':
            self._submerge_spacing += self._submerge_interval * (1 if event.shift else 10)
            self._refresh = True

        elif event.type == 'WHEELDOWNMOUSE':
            self._submerge_spacing -= self._submerge_interval * (1 if event.shift else 10)
            if self._submerge_spacing < 0: self._submerge_spacing = 0
            self._refresh = True

        if self._refresh == True:
            bpy.ops.ed.undo()
            self.subdivide_and_merge()
            context.scene['submerge_spacing'] = self._submerge_spacing
            self._refresh = False
       
        if event.type == 'MOUSEMOVE' and self._lbuttondown:
            if (event.mouse_prev_x != event.mouse_x or  event.mouse_prev_y != event.mouse_y):
                self._dragging = True
                self._submerge_spacing += (event.mouse_y - self._mouse_start[1]) * (0.0001 if event.shift else 0.001)
                if self._submerge_spacing < 0: self._submerge_spacing = 0
                self._mouse_start = (event.mouse_x, event.mouse_y)
                self._refresh = True

        return {'RUNNING_MODAL'}    
    
    def execute(self, context):
        # remember start and end point positions of open strokes
        self._startend.clear()
        gp = context.active_object
        for layer in gp.data.layers:
            if layer.hide == True or layer.lock == True: continue
            fr = layer.current_frame()
            if fr == None: continue
            for stroke in fr.drawing.strokes:
                if stroke.select and not stroke.cyclic:
                    self._startend.append((stroke.points[0].position, stroke.points[-1].position))
            
        # save select mode state and set mode to POINT to see point spacing
        self._init_select_mode = context.scene.tool_settings.gpencil_selectmode_edit
        self._init_sculpt_mask_point = context.scene.tool_settings.use_gpencil_select_mask_point
        self._init_sculpt_mask_stroke = context.scene.tool_settings.use_gpencil_select_mask_stroke
        
        context.scene.tool_settings.gpencil_selectmode_edit = 'POINT'
        context.scene.tool_settings.use_gpencil_select_mask_point = True

        bpy.ops.ed.undo_push(message = "SubMerge Stroke")

        self._submerge_spacing = context.scene.get('submerge_spacing')
        if not self._submerge_spacing: self._submerge_spacing = 0.0
        
        context.area.header_text_set("SubMerge Spacing: %.4f" % self._submerge_spacing)
        
        self.subdivide_and_merge()

        context.window_manager.modal_handler_add(self)
        context.window.cursor_modal_set("SCROLL_Y")
        
        return {'RUNNING_MODAL'}


class quickToggleFullScreenOperator(bpy.types.Operator):
    """Make view fullscreen. 
ESC or right click to exit fullscren.
"""
    
    bl_idname = "quicktools.togglefullscreen"    
    bl_label = "Quick Toggle FullScreen"
    
    _timer = None
    _original_area = None

    def show(self, context, isFull):
        if context.area:
            isFullScreen = context.window.width == context.area.width
        else:
            isFullScreen = True
            
        if not isFullScreen:
           context.window.cursor_modal_set("NONE")
        else:
           context.window.cursor_modal_restore()
        
        bpy.ops.screen.screen_full_area(use_hide_panels=True)
        bpy.ops.wm.window_fullscreen_toggle()

        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                for region in area.regions:
                    if region.type == "WINDOW":
                        for space in area.spaces:
                            if space.type == 'VIEW_3D':
                                override = {'screen' : context.screen, 'area' : area, 'region' : region, 'space' : space }
                                with context.temp_override(**override):
                                    bpy.context.space_data.overlay.show_overlays = isFullScreen
                                    bpy.context.space_data.show_gizmo = isFullScreen
                                    if isFullScreen: centerCamera(bpy.context)
                        
    def modal(self, context, event):
        if event.type == 'TIMER':
            for area in context.screen.areas:
                if area.type == 'VIEW_3D':
                    for region in area.regions:
                        if region.type == "WINDOW":
                            override = {'screen' : context.screen, 'area' : area, 'region' : region }
                            with context.temp_override(**override):
                                try:
                                    bpy.ops.view3d.view_center_camera()
                                    area.spaces[0].region_3d.view_camera_zoom=29
                                except:
                                    pass
            if self._timer:                    
                wm = context.window_manager
                wm.event_timer_remove(self._timer)
                self._timer = None
                        
        elif event.type in {'ESC', 'RIGHTMOUSE'} and not self._timer:
            self.show(context, True)
            return {'FINISHED'}

        return  {'PASS_THROUGH'}

    def execute(self, context):
        bpy.ops.view3d.view_center_camera()
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':        
                _original_area = context.area
        self.show(context, False)
        wm = context.window_manager
        self._timer = wm.event_timer_add(0, window=context.window)
        context.window_manager.modal_handler_add(self)
        
        return {'RUNNING_MODAL'} 
    

class QuickSelectPointsOperator(bpy.types.Operator):
    """
    Select Linked (all points on strokes with a point selected).
    Hold Shift when clicking this button to select in between points
    on strokes with 2 points selected. Press M to invert selection.
    Alt to deselect all strokes on all frames
    """
    
    bl_idname = "quicktools.selectpoints"
    bl_label = "Select Linked points / In between points"

    selected_strokes = [] # ( layer_index, stroke_index, selected_start, selected_end )
    invert_selection = False
    
    @classmethod
    def poll(self, context):
        if context.active_object == None or context.active_object.type != 'GREASEPENCIL': return False
        return context.mode == 'EDIT_GREASE_PENCIL' or context.mode == 'SCULPT_GREASE_PENCIL' or context.mode == 'VERTEX_GREASE_PENCIL'

    def modal(self, context, event):
        if event.type == 'LEFTMOUSE':
            context.window.cursor_modal_restore()
            context.workspace.status_text_set("")
            return {'FINISHED'}
        elif event.type =='M' and event.value == 'PRESS':
            self.invert_selection = not self.invert_selection
            self.setSelection(context)            

        return {'RUNNING_MODAL'}
    
    def setSelection(self, context):
        gp = context.active_object
        
        for selected_stroke in self.selected_strokes:
            stroke = gp.data.layers[selected_stroke[0]].current_frame().drawing.strokes[selected_stroke[1]]
            start_index = selected_stroke[2]
            end_index = selected_stroke[3]
            for idx in range(len(stroke.points)):
                
                if idx > start_index and idx < end_index:
                    stroke.points[idx].select = not self.invert_selection
                elif idx != start_index and idx != end_index:
                    stroke.points[idx].select = self.invert_selection
        
        gp.data.update_tag()
        context.area.tag_redraw()
        

    def execute(self, context):
        self.selected_strokes = []
        
        gp = context.active_object
        layer_index = -1
        
        for layer in gp.data.layers:
            layer_index += 1
            if layer.lock == True or layer.hide == True: continue
            stroke_index = -1
            frame = layer.current_frame()
            for stroke in frame.drawing.strokes:
                end_index = -1
                stroke_index += 1
                point_index = start_index = end_index = -1
                for point in stroke.points:
                    point_index += 1
                    if point.select:
                        if start_index == -1:
                            start_index = point_index
                        elif end_index >= 0: # check for more than 2 points selected on stroke
                            end_index = -1
                        else:
                            end_index = point_index

                if end_index != -1:
                    self.selected_strokes.append( (layer_index, stroke_index, start_index, end_index) )
                    
        if len(self.selected_strokes) == 0:
            return {'FINISHED'}
                    
        self.setSelection(context)
        context.window.cursor_modal_set("PICK_AREA")
        context.workspace.status_text_set("Select between points: M to invert selection: Click to finish.")
        context.window_manager.modal_handler_add(self)
                        
        return {'RUNNING_MODAL'}
    
    def invoke(self, context, event):
        if event.shift:
            return self.execute(context)
        elif event.alt:
            try: 
                print("Deselecting ALL strokes")
                gp = context.active_object
                for layer in gp.data.layers:
                    if layer.lock == True or layer.hide == True: continue
                    for frame in layer.frames:
                        for stroke in frame.drawing.strokes:
                            for point in stroke.points:
                                point.select = False
                            stroke.select = False
                gp.data.update_tag()
                context.area.tag_redraw()
            except: None
            return {'FINISHED'}
        else:
            try: bpy.ops.grease_pencil.select_linked()
            except: None
            return {'FINISHED'}


class quickHardnessOperator(bpy.types.Operator):
    """Middle mouse to adjust selected strokes' hardness.
Hold CTRL to adjust selected points' radius.
Hold SHIFT to adjust selected points' opacity.
Hold SHIFT+CTRL to adjust selected strokes' fill opacity.
Hold ALT to adjust selected strokes' rotation.
Left click to apply"""
    
    bl_idname = "quicktools.hardness"
    bl_label = "Stroke Hardness/Radius/Opacity"
    bl_options = {'REGISTER', 'UNDO'}
    selected_points = []
    selected_strokes = []
    _interval = 0
    _direction = 0
    _mouse_start = None
    _lbuttondown = False
    _refresh = False
    _dragging = False
    
    @classmethod
    def poll(self, context):
        if context.active_object == None or context.active_object.type != 'GREASEPENCIL': return False
        return context.mode == 'SCULPT_GREASE_PENCIL' or (context.mode == 'EDIT_GREASE_PENCIL' and \
            context.workspace.tools.from_space_view3d_mode(context.mode).idname == 'builtin.select_box')
    
    def get_selected_points(self, context):
        if context.active_object == None or context.active_object.type != 'GREASEPENCIL':
            return []
        return [ p for lr in context.active_object.data.layers if not lr.lock and not lr.hide for s in lr.current_frame().drawing.strokes 
            if s.select for p in s.points if p.select]
            
    def get_selected_strokes(self, context):
        gp = context.active_object
        
        return [s for lr in gp.data.layers if not lr.lock for s in lr.current_frame().drawing.strokes if s.select]

    def modal(self, context, event):

        if event.type == "LEFTMOUSE" and event.value == 'RELEASE':
            self._lbuttondown = False

            context.space_data.overlay.use_gpencil_edit_lines=True
            
            if not self._dragging:
                context.area.header_text_set(None)
                context.window.cursor_modal_restore()

                return {'FINISHED'}

        if event.type in {'RIGHTMOUSE', 'ESC'}:
            context.area.header_text_set(None)
            bpy.ops.ed.undo()

            context.window.cursor_modal_restore()

            return {'CANCELLED'}

        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            context.space_data.overlay.use_gpencil_edit_lines=True
            self._mouse_start = (event.mouse_x, event.mouse_y)
            self._lbuttondown = True
            self._dragging = False
            
        elif event.type == 'WHEELUPMOUSE':
            if self._direction == 0:
                self._interval = 0
            self._direction = 1
                
            self._interval +=  (0.001 if event.shift else 0.001)
            self._interval = min(1, self._interval)
            self._refresh = True

        elif event.type == 'WHEELDOWNMOUSE':
            if self._direction == 1:
                self._interval = 0
            self._direction = 0

            self._interval -=  (0.001 if event.shift else 0.001)
            self._interval = max(-1, self._interval)
            self._refresh = True

        if event.type == 'MOUSEMOVE' and self._lbuttondown:
            if (event.mouse_prev_x != event.mouse_x or  event.mouse_prev_y != event.mouse_y):
                self._dragging = True
                if event.mouse_y < self._mouse_start[1] and self._direction == 0:
                    self._interval = 0
                    self._direction = 1
                elif event.mouse_y > self._mouse_start[1] and self._direction == 1:
                    self._interval = 0
                    self._direction = 0
                
                self._interval += (event.mouse_y - self._mouse_start[1]) * 0.0001
                if self._interval < -1: self._interval = -1
                if self._interval > 1: self._interval = 1
                self._mouse_start = (event.mouse_x, event.mouse_y)
                self._refresh = True

        if self._refresh == True:
            self._refresh = False
            context.space_data.overlay.use_gpencil_edit_lines=False
            
            if event.shift and event.ctrl:
                for s in context.active_object.data.layers.active.current_frame().drawing.strokes:
                    if s.fill_opacity == 0: s.fill_opacity = 1
                for s in self.selected_strokes:
                    opacity = s.fill_opacity + self._interval 
                    opacity = min(max(opacity, 0), 1)
                    s.fill_opacity = opacity
                if len(self.selected_strokes) > 0:
                    s = self.selected_strokes[0]
                    context.area.header_text_set("Fill Opacity: %.4f" % s.fill_opacity)
            elif event.shift:
                sp = None
                for p in self.selected_points:
                    opacity = p.opacity + self._interval
                    opacity = min(max(opacity, 0), 1)
                    p.opacity = opacity
                    if sp == None: sp = p
                if sp: context.area.header_text_set("Opacity: %.4f" % sp.opacity)
            elif event.ctrl:
                sp = None
                for p in self.selected_points:
                    radius = p.radius + self._interval
                    radius = max(radius, 0)
                    p.radius = radius
                    if sp == None: sp = p
                if sp: context.area.header_text_set("Radius: %.4f" % sp.radius)
            elif event.alt:
                sp = None
                for p in self.selected_points:
                    rotation = p.rotation + self._interval
                    p.rotation = rotation
                    if sp == None: sp = p
                if sp: context.area.header_text_set("Rotation: %.4f" % sp.rotation)
            else:
                for s in self.selected_strokes:
                    softness = s.softness + self._interval
                    softness = min(max(softness, 0), 1)
                    s.softness = softness
                if len(self.selected_strokes) > 0:
                    s = self.selected_strokes[0]
                    context.area.header_text_set("Hardness: %.4f" % s.softness)
        
        return {'RUNNING_MODAL'}
                
    
    def execute(self, context):
        bpy.ops.ed.undo_push(message = quickHardnessOperator.bl_label)
        self.selected_points = self.get_selected_points(context)    
        self.selected_strokes = self.get_selected_strokes(context)    
        context.window.cursor_modal_set("SCROLL_Y")
        context.window_manager.modal_handler_add(self)
        
        return {'RUNNING_MODAL'}
    
    def cancel(self, context):
        context.area.header_text_set(None)
        context.window.cursor_modal_restore()
    
    
class quickInterpolateStroke(bpy.types.Operator):
    """Interpolate 2 selected strokes between keyframes when Multiframe editing
Hold SHIFT to flip stroke direction
Hold CTRL to smooth interpolated strokes
"""
    bl_idname = "quicktools.interpolate_stroke"
    bl_label = "QuickTools - Interpolate Stroke"
    
    shift_pressed = False
    
    @classmethod
    def poll(self, context):
        if context.active_object == None or context.active_object.type != 'GREASEPENCIL': return False
        if not (context.mode == 'SCULPT_GREASE_PENCIL' or context.mode == 'EDIT_GREASE_PENCIL'):
            return False
        return context.tool_settings.use_grease_pencil_multi_frame_editing
    
    def execute(self, context):
        gp = context.active_object
        if gp.data.layers.active.lock == True or gp.data.layers.active.lock == True: 
            self.report({'ERROR'}, message="Active layer is locked or hidden")
            return {'CANCELLED'}    
    
        prev_keyframe = next_keyframe = None
        start_stroke = end_stroke = None
        prev_idx = next_idx = start_stroke_idx = None
        
        C = bpy.context
        gp = C.active_object
        
        start_frame_number = C.scene.frame_current
        
        for idx, frame in enumerate(gp.data.layers.active.frames):
            if frame.keyframe_type != 'KEYFRAME': continue
            if frame.frame_number <= C.scene.frame_current:
                prev_keyframe = frame
                prev_idx = idx
            elif frame.frame_number > C.scene.frame_current:
                next_keyframe = frame
                next_idx = idx
                break
        if prev_keyframe == None:
            self.report({'WARNING'}, "No start keyframe found")
        elif next_keyframe == None:
            self.report({'WARNING'}, "No end keyframe found")
        else:
            num_frames = next_keyframe.frame_number - prev_keyframe.frame_number
            for idx, stroke in enumerate(prev_keyframe.drawing.strokes):
                if stroke.select == True:
                    start_stroke = stroke
                    start_stroke_idx = idx
                    break
            for stroke in next_keyframe.drawing.strokes:
                if stroke.select == True:
                    end_stroke = stroke
                    break
            if start_stroke == None and end_stroke == None:
                self.report({'WARNING'}, "No start and end strokes selected on surronding KEYFRAMES")
            elif start_stroke == None:
                self.report({'WARNING'}, "No start stroke selected on previous KEYFRAME")
            elif end_stroke == None:
                self.report ({'WARNING'}, "No end stroke selected next KEYFRAME")
            else:
                for lr in gp.data.layers:
                    for fr in lr.frames:
                        for s in fr.drawing.strokes:
                            for p in s.points:
                                p.select = False
                            s.select = False
                            
                if self.shift_pressed:
                    end_stroke.select = True
                    bpy.ops.grease_pencil.stroke_switch_direction()
                    end_stroke.select = False

                start_stroke.select = True
                bpy.ops.grease_pencil.copy()
                end_stroke.select = True

                bpy.context.scene.frame_set(prev_keyframe.frame_number)
                
                for idx in range(num_frames - 1):
                    C.scene.frame_current += 1
                    bpy.ops.grease_pencil.paste()
                    bd = gp.data.layers.active.frames[prev_idx + idx + 1]
                    frame_stroke = bd.drawing.strokes[-1]
                    bd.keyframe_type = 'BREAKDOWN'
                    
                    frame_stroke.fill_opacity = \
                        Vector((start_stroke.fill_opacity, 0, 0)).lerp(Vector((end_stroke.fill_opacity, 0, 0)), idx / num_frames)[0]
                    fc = Vector(start_stroke.fill_color).lerp(Vector(end_stroke.fill_color), idx / num_frames)
                    frame_stroke.fill_color = (fc[0], fc[1], fc[2], 1)
                    
                    for pdx, s_point in enumerate(start_stroke.points):
                        epx = int( pdx / len(frame_stroke.points) * len(end_stroke.points))
                        if epx >= len(end_stroke.points): continue
                        sp = Vector(s_point.position)
                        ep = Vector(end_stroke.points[epx].position)
                        np = sp.lerp(ep, (idx + 1) / num_frames)
                        frame_stroke.points[pdx].position = np
                        
                        sw = Vector((s_point.radius, 0, 0))
                        ew = Vector((end_stroke.points[epx].radius, 0, 0))
                        nw = sw.lerp(ew, (idx + 1) / num_frames)
                        frame_stroke.points[pdx].radius = nw[0]

                        so = Vector((s_point.opacity, 0, 0))
                        eo = Vector((end_stroke.points[epx].opacity, 0, 0))
                        no = so.lerp(eo, (idx + 1) / num_frames)
                        frame_stroke.points[pdx].opacity = no[0]
                        
                        sc = Vector(s_point.vertex_color)
                        ec = Vector(end_stroke.points[epx].vertex_color)
                        nc = sc.lerp(ec, (idx + 1) / num_frames)
                        frame_stroke.points[pdx].vertex_color = (nc[0], nc[1], nc[2], 1)

                    if self.ctrl_pressed:
                        bpy.ops.quicktools.set_tool(args='OPS|SMOOTH')
                    
                bpy.context.scene.frame_set(start_frame_number)
                
        return {'FINISHED'}


    def invoke(self, context, event):
        self.shift_pressed = event.shift
        self.ctrl_pressed = event.ctrl
        return self.execute(context)
    


def get_addon_directory(addon_name):
    for mod in addon_utils.modules():
        if mod.bl_info['name'] == addon_name:
            return os.path.split(mod.__file__)[0]
    return None


# Define the EnumProperty with a callback function
def file_list_callback(self, context):
    addon_dir = get_addon_directory('Grease Pencil QuickTools_v3')
    json_dir = os.path.join(addon_dir, 'dat')
    # Get a list of files in the directory
    files = [f for f in os.listdir(json_dir) if os.path.isfile(os.path.join(json_dir, f))]
    # Create a list of tuples for the EnumProperty
    items = [(f, f, "") for f in files if f[-4:] == 'json']
    return items


class quickGPTextOperator(bpy.types.Operator):
    """Text color = Active brush color
Shadow color = Active brush secondary color
Uses active stroke only enabled material.
"""
    bl_label = "Add text strokes to layer"
    bl_idname = "quicktools.gptext"
    bl_options = {'REGISTER', 'UNDO_GROUPED'}
    
    _handle = None
    _cx = _cy = _xoff = _yoff = _size = _align = _radius = 0
    _json_file = _text = ""
    _shadow_offset = -0.025
    _charData = []

    gptext_shadow : bpy.props.BoolProperty(name="S", default=0)
    gptext_text : bpy.props.StringProperty ( name = "", description = "User text",  default = "Lorem ipsum dolor sit amet,\\nconsectetur adipiscing elit" )
    gptext_xpos : bpy.props.FloatProperty( name="X", description="X position", default=0.0)
    gptext_ypos : bpy.props.FloatProperty( name="Y", description="Y position", default=0.0)
    gptext_cx : bpy.props.FloatProperty( name="CX", description="Character spacing", default=1)
    gptext_cy : bpy.props.FloatProperty( name="CY", description="Line spacing", default=5)
    gptext_size : bpy.props.FloatProperty( name="Size", description="Size", default=1)
    gptext_thickness : bpy.props.IntProperty( name="Thickness", description="Thickness", default=20)
    enum_items = (('0','','','ANCHOR_LEFT',0),('1','','','ANCHOR_CENTER',1),('2','','','ANCHOR_RIGHT',2))
    gptext_align : bpy.props.EnumProperty(items = enum_items, default=1)
    gptext_json : bpy.props.EnumProperty( items=file_list_callback, name="Style", description="Select a file from the list")
    

    @classmethod
    def poll(self, context):
        if context.active_object == None or context.active_object.type != 'GREASEPENCIL': return False
        if context.mode != 'PAINT_GREASE_PENCIL': return False
        if context.active_object.data.layers.active.lock: return False
        if context.active_object.data.layers.active.hide: return False
        return (context.active_object and context.active_object.type == 'GREASEPENCIL')

    def getMinMax(self, ch):
        ch_min = 999
        ch_max = -999       
        
        if len(self._charData) == 0:
            return ch_min, ch_max
        
        data = self._charData.get(ch)

        if data == None:
            return ch_min, ch_max

        for ss in data:
            if len(ss) < 2:
                continue
            if isinstance(ss[0], float):
                ch_min = min(ch_min, ss[0])
                ch_max = max(ch_max, ss[0])
            else:
                for pp in ss:
                    ch_min = min(ch_min, pp[0])
                    ch_max = max(ch_max, pp[0])
                
        return ch_min, ch_max

    def getStringWidth(self, string, spacing, defaultWidth):
        width = 0
        for ch in string:
            ch_min, ch_max = self.getMinMax(ch)
            if ch_max != -999:
                if width > 0: width += spacing
                width += abs(ch_max - ch_min)
            else:
                width += defaultWidth
                
        return width
    
    def buildString(self, context):
        
        stringStrokes = []
        
        xoff = self.gptext_xpos
        yoff = self.gptext_ypos
        scale = self.gptext_size * 0.1
        spacing = self.gptext_cx
        defaultWidth = 1.7
        
        lines = self.gptext_text.split("\\n")

        for string in lines:
            xoff = self.gptext_xpos
            stringWidth = self.getStringWidth(string, spacing, defaultWidth)

            if self.gptext_align == '1':
                xoff -= stringWidth / 2 * scale
            elif self.gptext_align == '2':
                xoff -= stringWidth * scale
            
            offset = 0
            
            for idx,ch in enumerate(string):
                ch_min, ch_max = self.getMinMax(ch)
                
                if ch_max != -999:
                    if idx > 0: 
                        offset += spacing

                    strokePoints = []
                    data = self._charData.get(ch)
                    
                    for stroke in data:
                        if len(stroke) < 2: 
                            continue
                        
                        if isinstance(stroke[0], float):
                            px = xoff + (stroke[0] + offset - ch_min) * scale
                            py = yoff + stroke[1] * scale
                            strokePoints.append( (px, py) )
                        else:
                            for point in stroke:
                                if len(point) == 2:
                                    px = xoff + (point[0] + offset - ch_min) * scale
                                    py = yoff + point[1] * scale
                                    strokePoints.append( (px, py) )
                                    
                        stringStrokes.append(strokePoints)
                        strokePoints = []
                        
                    if len(strokePoints) > 1:
                        stringStrokes.append(strokePoints)
                        
                    offset += abs(ch_max - ch_min)
                    
                else:
                    offset += defaultWidth
                    
            yoff -= self.gptext_cy * scale
                    
        return stringStrokes

    def load_charData(self, context):
        # jsonFile = os.path.join(bpy.utils.script_path_user(), 'addons', self.gptext_json)
        mod_file = [mod.__file__ for mod in addon_utils.modules() if mod.bl_info['name'] == 'Grease Pencil QuickTools_v3']
        if len(mod_file) == 0:
            self.report("{WARNING}", "QuickTool_v3 extension location not found")
            return {'CANCELLED'}
        directory = os.path.split(mod_file[0])[0]
        jsonFile = os.path.join(directory, 'dat', self.gptext_json)
            
        if not os.path.exists(jsonFile):
            self.report({'ERROR'}, "Missing: " + jsonFile)
            return {'CANCELLED'}
        
        inputData = open(jsonFile, "rt")
        self._charData = json.load(inputData)
        self._strokes = self.buildString(context)


    def draw_callback_px(self, context):
        
        redraw = False
        
        if self.gptext_thickness != self._radius: redraw = True
        if self.gptext_xpos != self._xoff: redraw = True    
        if self.gptext_ypos != self._yoff: redraw = True
        if self.gptext_cx != self._cx:redraw = True
        if self.gptext_cy != self._cy:redraw = True
        if self.gptext_size != self._size:redraw = True
        if self.gptext_align != self._align: redraw = True
        if self.gptext_text != self._text: redraw = True
        if self.gptext_json != self._json_file: redraw = True

        if redraw == True:
            self._radius = self.gptext_thickness
            self._xoff = self.gptext_xpos
            self._yoff = self.gptext_ypos
            self._cx = self.gptext_cx
            self._cy = self.gptext_cy
            self._size = self.gptext_size
            self._align = self.gptext_align
            self._text = self.gptext_text
            self.load_charData(context)
            self._json_file = self.gptext_json
            self._strokes = self.buildString(context)
            redraw = False
            
        area = context.area
        space = area.spaces[0]
        
        for region in area.regions:
            if region.type == 'WINDOW':
                break

        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        gpu.state.blend_set('ALPHA')

        lineWidth = int(self.gptext_thickness / 4)
        gpu.state.line_width_set( lineWidth )

        for rdx in range(1 + 1 * self.gptext_shadow):    
            if self.gptext_shadow and rdx == 0:
                clr = context.tool_settings.gpencil_paint.brush.secondary_color
                yoffset = lineWidth / 200
            else:
                clr = context.tool_settings.gpencil_paint.brush.color
                yoffset = 0

            clr = (s2lin(clr.r), s2lin(clr.g), s2lin(clr.b), 1)
            shader.uniform_float("color", clr)

            for stroke in self._strokes:
                stroke2d = []
                for point in stroke:
                    p = view3d_utils.location_3d_to_region_2d(region, space.region_3d, (point[0] + yoffset,0 ,point[1] - yoffset))
                    stroke2d.append(p)
                batch = batch_for_shader(shader, 'LINE_STRIP', {"pos": stroke2d})
                batch.draw(shader)    
                
        # restore opengl defaults
        gpu.state.line_width_set(1.0)
        gpu.state.blend_set('NONE')
        
                
    def draw(self, context):
        row = self.layout.row()
        row.prop(self, 'gptext_xpos')
        row.prop(self, 'gptext_ypos')
        row = self.layout.row()
        row.prop(self, 'gptext_cx')
        row.prop(self, 'gptext_cy')
        row = self.layout.row()
        row.prop(self, 'gptext_size')    
        row.prop(self, 'gptext_thickness')
        row = self.layout.row()
        row.prop(self, 'gptext_shadow', icon='EVENT_S', icon_only=True)
        row.prop(self, 'gptext_align', expand=True)
        row.prop(self, "gptext_json")
        row = self.layout.row()
        row.prop(self, 'gptext_text')
        
    def invoke(self, context, event):
        if self.gptext_json == '':
            addon_dir = get_addon_directory('Grease Pencil QuickTools_v3')
            if addon_dir == None:
                self.report({'ERROR'}, "Grease Pencil QuickTools_v3 package missing" )
            else:
                self.report({'ERROR'}, "Missing gptext.json files in " + os.path.join(addon_dir, 'dat'))
            return {'CANCELLED'}

        gp = context.active_object
        
        mat_index = gp.active_material_index
        if mat_index == None: return {'CANCELLED'}
        mat_name = gp.material_slots[mat_index].name
        gp_mat = bpy.data.materials[mat_name]
        if gp_mat.grease_pencil.show_stroke == False or gp_mat.grease_pencil.show_fill == True:
            self.report({'ERROR'}, "Active material required with 'Stroke' only enabled")
            return {'CANCELLED'}
            
        self.load_charData(context)   
        self._json_file = self.gptext_json             
        context.area.tag_redraw()
        x = context.area.x + int(context.area.width / 2)
        y = context.area.y
        context.window.cursor_warp(x,y + 120);
        self._handle = bpy.types.SpaceView3D.draw_handler_add(self.draw_callback_px, (context,), 'WINDOW', 'POST_PIXEL')
        return context.window_manager.invoke_props_dialog(self)

    def cancel(self, context):
        if self._handle: bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
        self._handle = None
        context.area.tag_redraw()
        return None

    def execute(self, context):
        if self._handle: bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
        self._handle = None
        
        gp = bpy.context.active_object
        layer = gp.data.layers.active
        mat_index = context.active_object.active_material_index
            
        matIndex = 0
        fillColor = (0,0,0,1)
        vertexColor = context.tool_settings.gpencil_paint.brush.color
        secondaryColor = context.tool_settings.gpencil_paint.brush.secondary_color
        lineWidth = self.gptext_thickness / 1000

        frame = gp.data.layers.active.current_frame()

        if frame == None:
            bpy.ops.grease_pencil.insert_blank_frame()
        elif frame.frame_number != context.scene.frame_current:
            bpy.ops.grease_pencil.insert_blank_frame()
        for frame in gp.data.layers.active.frames:
            if frame.frame_number == context.scene.frame_current:
                break
        if frame == None:
            self.report({'ERROR'}, message="Error adding keyframe to layer")
            return {'CANCELLED'}
            
        for rdx in range(1 + 1 * self.gptext_shadow):    
            if self.gptext_shadow and rdx == 0:
                clr = context.tool_settings.gpencil_paint.brush.secondary_color
                yoffset = lineWidth
            else:
                clr = context.tool_settings.gpencil_paint.brush.color
                yoffset = 0
            
            for stroke in self._strokes:
                frame.drawing.add_strokes([len(stroke)])
                newStroke = frame.drawing.strokes[-1]
                newStroke.material_index = matIndex
                newStroke.fill_color = fillColor
                for idx, point in enumerate(stroke):
                    newStroke.points[idx].radius = lineWidth
                    newStroke.points[idx].opacity = 1
                    newStroke.points[idx].position = ( point[0] + yoffset, 0, point[1] - yoffset )
                    newStroke.points[idx].vertex_color = (s2lin(clr.r), s2lin(clr.g), s2lin(clr.b), 1)
                
        bpy.ops.ed.undo_push(message = quickGPTextOperator.bl_label)
        return {'FINISHED'}



class quickAlignOperator(bpy.types.Operator):
    bl_idname = "quicktools.align_points"
    bl_label = "Align Selection"
    bl_options = {'UNDO'}
    
    selectedPoint = None
    selected_points = []
    align : IntProperty(default=0)

    @classmethod
    def description(cls, context, properties):
        match properties.align:
            case 1:
                return "Align selected points horizontally to clicked selected point.\nSHIFT to keep relative offsets"
            case 2: 
                return "Align selected points vertically to clicked selected point.\nSHIFT to keep relative offsets"
            
        return "Converge selected points to clicked selected point.\nSHIFT to keep relative offsets"

    @classmethod
    def poll(self, context):
        return (context.mode == 'SCULPT_GREASE_PENCIL' or context.mode == 'EDIT_GREASE_PENCIL')
    
    def get_selected_points(self, context):
        if context.active_object == None or context.active_object.type != 'GREASEPENCIL': return []
        use_multiedit = ume = context.tool_settings.use_grease_pencil_multi_frame_editing
        gp = context.active_object
        return [p
            for lr in gp.data.layers
                if not lr.lock and not lr.hide  #Respect layer locking and visibility
                    for fr in ([fr for fr in lr.frames if fr.select or fr == lr.current_frame()] if use_multiedit else [lr.current_frame()])    #Respect multiframe editing settings
                        for s in fr.drawing.strokes
                            if s.select
                                for p in s.points
                                    if p.select]
    
    def modal(self, context, event):
        
        self.shift_key = event.shift
        
        if event.type == "MOUSEMOVE":
            pos = view3d_utils.region_2d_to_location_3d(context.region, context.space_data.region_3d, 
                (event.mouse_region_x, event.mouse_region_y), (0,0,0))

            self.selectedPoint = None
            gp = context.active_object.data
            
            for pt in self.selected_points:
                v = Vector((pos[0] - pt.position[0], 0, pos[2] - pt.position[2]))
                if v.length < 0.04:
                    self.selectedPoint = pt
                    break

            if self.selectedPoint:
               context.window.cursor_modal_set("CROSSHAIR")
            else:
                context.window.cursor_modal_set("PAINT_CROSS")

        elif event.type == "LEFTMOUSE":
            context.window.cursor_modal_restore()
            context.window.cursor_modal_restore()

            if self.selectedPoint:
                if self.shift_key:
                    vOffset = Vector((99999, 0, 99999))
                    for p in self.selected_points:
                        if p == self.selectedPoint: continue
                        if self.align == 1:
                            v = Vector((self.selectedPoint.position[0] - p.position[0], 0, 0))
                        elif self.align == 2:
                            v = Vector((0, 0, self.selectedPoint.position[2] - p.position[2]))
                        else:
                            v = Vector((self.selectedPoint.position[0] - p.position[0], 0, self.selectedPoint.position[2] - p.position[2]))
                        if v.length < vOffset.length:
                            vOffset = v

                for p in self.selected_points:
                    if p == self.selectedPoint: continue
                    if self.align == 0 or self.align == 1:
                        p.position[0] = self.selectedPoint.position[0] if not self.shift_key else p.position[0] + vOffset.x
                    if self.align == 0 or self.align == 2:
                        p.position[2] = self.selectedPoint.position[2] if not self.shift_key else p.position[2] + vOffset.z
                    
                return {'FINISHED'}
            return {'CANCELLED'}
            
        elif event.type in {'RIGHTMOUSE', 'ESC'}:
            context.window.cursor_modal_restore()

            return {'CANCELLED'}
        
        return {'RUNNING_MODAL'}    
    
    def execute(self, context):
        self.selected_points = self.get_selected_points(context)
        context.window.cursor_modal_set("PAINT_CROSS")
        context.window_manager.modal_handler_add(self)
        
        return {'RUNNING_MODAL'}

    def invoke(self, context, event):
        self.shift_key = event.shift
        return self.execute(context)                
    
    

class quickTaperStrokeOperator(bpy.types.Operator):
    """
"""

    bl_idname = "quicktools.taper_thickness"
    bl_label = "Taper in/out stroke thickness"
    bl_options = {'REGISTER', 'UNDO'}
    selected_points = []
    selected_strokes = []

    @classmethod
    def description(cls, context, properties):
        return "SHIFT to Taper In only.\nCTRL to Taper Out only"

    
    @classmethod
    def poll(self, context):
        if context.active_object == None or context.active_object.type != 'GREASEPENCIL': return False
        return (context.mode == 'SCULPT_GREASE_PENCIL' or context.mode == 'EDIT_GREASE_PENCIL')
    
    def easeOutQuad(self, t):
        return -t * (t - 2);
    
    def easeOutCubic(self, t):
        return 1 - pow(1 - t, 3)

    def InOutQuadBlend(self, t):
        if t <= 0.5: return 2 * t * t
        t -= 0.5
        return 2 * t * (1.0 - t) + 0.5
    
    def SimpleBlend(self, t):
        return -t * (t - 2)
    
    def ParametricBlend(self, t):
        sqr = t * t
        return sqr / (2.0 * (sqr - t) + 1.0)

    def BezierBlend(self, t):
        return t * t * (3.0 - 2.0 * t)

    def invoke(self, context, event):
        taperIn = (event.shift == False and event.ctrl == False) or event.shift
        taperOut = (event.shift == False and event.ctrl == False) or event.ctrl
        self.shift_key = event.shift
        gp = context.active_object
        bpy.ops.ed.undo_push(message = "Ease In/Out")

        use_multiedit = ume = context.tool_settings.use_grease_pencil_multi_frame_editing        
        strokes = [s for lr in gp.data.layers if not lr.lock and not lr.hide for fr in ([fr for fr in lr.frames if fr.select or fr == lr.current_frame()] if use_multiedit else [lr.current_frame()]) for s in fr.drawing.strokes if s.select]

        segments = []
        for stroke in strokes:
            pts = []
            for pt in stroke.points:
                if pt.select:
                    pts.append(pt)
                elif len(pts) > 0:
                    segments.append(pts)
                    pts = []
            if len(pts) > 0:
                segments.append(pts)            
        for segment in segments:
            pt1 = None
            segment_length = 0
            midRadius = segment[ int(len(segment) / 2) ].radius
            for pt2 in segment:
                if pt1:
                    segment_length += (Vector(pt2.position) - Vector(pt1.position)).length
                pt1 = pt2
            pt1 = run_length = 0
            for pt2 in segment:
                if pt1: run_length += (Vector(pt2.position) - Vector(pt1.position)).length
                if run_length < segment_length / 2 and taperIn and (segment_length / 2) > 0:
                    slope = run_length / (segment_length / 2)
                    pt2.radius = self.easeOutQuad(slope) * midRadius
                if run_length >= segment_length / 2 and taperOut and (segment_length / 2) > 0:
                    slope = (segment_length - run_length) / (segment_length / 2)
                    pt2.radius = self.easeOutQuad(slope) * midRadius
                pt1 = pt2
            
        return {'FINISHED'}

        
            
class QuickToolsSetToolOperator(bpy.types.Operator):
    args : bpy.props.StringProperty()    
    """Tooltip"""
    bl_idname = "quicktools.set_tool"
    bl_label = ""
    
    shift_key = False
    
    @classmethod
    def description(cls, context, properties):
        tooltips = dict(LINKED = 'Select all points on selected strokes.\nSHIFT to Select Between selected points (M to invert)',
            LAYER = 'Highlight layer selected stroke is on',
            FILL = "Set fill color of all selected strokes with active color",
            MFE = 'Multiframe Editing'
        )

        args = properties.args.split('|')
        
        if tooltips.get(args[1]):
            return tooltips.get(args[1])

        return args[1]    

    @classmethod
    def poll(cls, context):
        return True
    
    def invoke(self, context, event):
        self.shift_key = event.shift
        return self.execute(context)
    
    def execute(self, context):
        _mode, _tool = self.args.split('|')
        
        if _mode == 'OPS':
            if _tool == 'OBJECT_MODE':
                try: bpy.ops.object.mode_set(mode='OBJECT')
                except: pass
            elif _tool == 'UNDO':
                try: bpy.ops.ed.undo()
                except: None
            elif _tool == 'REDO':
                try: bpy.ops.ed.redo()
                except: None
            elif _tool == 'DELETE':
                bpy.ops.grease_pencil.delete()
            elif _tool == 'DISSOLVE':
                bpy.ops.grease_pencil.dissolve()
            elif _tool == 'FULLSCREEN':
                bpy.ops.quicktools.togglefullscreen()
            elif _tool == 'VIEW_BOUNDS':
                centerCamera(context)
            elif _tool == 'SCULPT_POINT':
                 bpy.context.scene.tool_settings.use_gpencil_select_mask_point = not bpy.context.scene.tool_settings.use_gpencil_select_mask_point
            elif _tool == 'SCULPT_STROKE':
                 bpy.context.scene.tool_settings.use_gpencil_select_mask_stroke = not bpy.context.scene.tool_settings.use_gpencil_select_mask_stroke
            elif _tool == "DRAW_ONBACK":
                bpy.context.scene.tool_settings.use_gpencil_draw_onback = not bpy.context.scene.tool_settings.use_gpencil_draw_onback
            elif _tool == "MFE":
                context.tool_settings.use_grease_pencil_multi_frame_editing = not context.tool_settings.use_grease_pencil_multi_frame_editing
            elif _tool == "DRAW_ADDITIVE":
                context.tool_settings.use_gpencil_draw_additive = not context.tool_settings.use_gpencil_draw_additive
            elif _tool == 'FILL':
                gp = context.active_object

                if context.mode == 'VERTEX_GREASE_PENCIL':
                    brush = context.tool_settings.gpencil_vertex_paint.brush
                else:
                    brush = context.tool_settings.gpencil_paint.brush

                if brush != None:
                    clr = brush.color
                    fill_color = ( s2lin(clr[0]), s2lin(clr[1]), s2lin(clr[2]), 1)
                    
                    use_multiedit = ume = context.tool_settings.use_grease_pencil_multi_frame_editing        
                    strokes = [s for lr in gp.data.layers if not lr.lock and not lr.hide for fr in ([fr for fr in lr.frames if fr.select or fr == lr.current_frame()] if use_multiedit else [lr.current_frame()]) for s in fr.drawing.strokes]
                    
                    for stroke in strokes:
                        if stroke.fill_opacity == 0: stroke.fill_opacity = 1
                        if stroke.select:
                            stroke.fill_color = fill_color
                            
            else: # change to edit mode to run stroke commands, return to previous mode after
                _mode = context.active_object.mode
                bpy.ops.object.mode_set(mode='EDIT')

                try:
                    if _tool == 'JOIN':
                        bpy.ops.grease_pencil.join_selection(type='JOIN')
                    elif _tool == 'CLOSE':
                        bpy.ops.grease_pencil.cyclical_set()
                    elif _tool == 'EDIT_POINT':
                        bpy.ops.grease_pencil.set_selection_mode(mode='POINT')
                    elif _tool == 'EDIT_STROKE':
                        bpy.ops.grease_pencil.set_selection_mode(mode='STROKE')
                    elif _tool == 'SMOOTH':
                        bpy.ops.grease_pencil.stroke_smooth(iterations=5, smooth_ends=False, keep_shape=True)
                    elif _tool == 'SUBDIVIDE':
                        bpy.ops.grease_pencil.stroke_subdivide()
                    elif _tool == 'BRING_TO_FRONT':
                        bpy.ops.grease_pencil.reorder(direction='TOP')
                    elif _tool == 'BRING_FORWARD':
                        bpy.ops.grease_pencil.reorder(direction='UP')
                    elif _tool == 'SEND_BACKWARD':
                        bpy.ops.grease_pencil.reorder(direction='DOWN')
                    elif _tool == 'SEND_TO_BACK':
                        bpy.ops.grease_pencil.reorder(direction='BOTTOM')
                except:
                    pass
                
                bpy.ops.object.mode_set(mode=_mode)
            
        else:

            context.tool_settings.unified_paint_settings.use_unified_color = False
            
            if context.mode == 'VERTEX_GREASE_PENCIL':
                brush = context.tool_settings.gpencil_vertex_paint.brush
            else:
                brush = context.tool_settings.gpencil_paint.brush
            
            try:    
                bpy.ops.object.mode_set(mode=_mode)
            except Exception as e:
                self.report({'ERROR'}, f"An error occured: {str(e)}")
                return {'CANCELLED'}
            
            type, brush_name = _tool.split('.')
            
            if brush_name == "brush":
                bpy.ops.wm.tool_set_by_id(name=_tool)
                brush_name = "Ink Pen"
                
            asset_blends = []
            asset_brushes = []
            
            user_library = bpy.context.preferences.filepaths.asset_libraries.get('User Library')
            
            if user_library:
                for fp in Path(user_library.path).glob("**/*.blend"):
                    asset_blends.append(fp)        
                    asset_brushes.append(fp.name.replace(".asset.blend",""))
            
            if asset_brushes.count(brush_name) > 0:
                idx = asset_brushes.index(brush_name)
                ass_blend = asset_blends[idx].relative_to(user_library.path)
                ass_id = os.path.join(ass_blend, "Brush", brush_name)
                bpy.ops.brush.asset_activate(asset_library_type='CUSTOM', asset_library_identifier="User Library", \
                    relative_asset_identifier=ass_id)
            else:
                library = 'ESSENTIALS'
                type, brush_name = _tool.split('.')
                
                if _mode == 'SCULPT_GREASE_PENCIL' and type != 'builtin':
                    brush_dir = os.path.join('brushes', 'essentials_brushes-gp_sculpt.blend', 'Brush', brush_name)
                    bpy.ops.brush.asset_activate(asset_library_type=library, relative_asset_identifier=brush_dir)
                elif _mode == 'VERTEX_GREASE_PENCIL': 
                    brush_dir = os.path.join('brushes', 'essentials_brushes-gp_vertex.blend', 'Brush', brush_name)
                    bpy.ops.brush.asset_activate(asset_library_type=library, relative_asset_identifier=brush_dir)
                else:
                    bpy.ops.wm.tool_set_by_id(name=_tool)

            if brush:
                if bpy.context.tool_settings.gpencil_paint.brush:
                    bpy.context.tool_settings.gpencil_paint.brush.color = brush.color
                    bpy.context.tool_settings.gpencil_paint.eraser_brush.color = brush.color
                if bpy.context.tool_settings.gpencil_vertex_paint.brush:
                    bpy.context.tool_settings.gpencil_vertex_paint.brush.color = brush.color
                if bpy.context.tool_settings.gpencil_sculpt_paint.brush:
                    bpy.context.tool_settings.gpencil_sculpt_paint.brush.color = brush.color
                
                for b in bpy.data.brushes:
                    b.color = brush.color
                                        
        return {'FINISHED'}


class QuickToolsPanel(bpy.types.Panel):
    """Grease Pencil QuickTools panel in the sidebar"""
    bl_label = "QuickTools v3"
    bl_idname = "OBJECT_PT_quicktools"

    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "QuickTools"


    @classmethod
    def poll(cls, context):
        if context.active_object and context.active_object.type != 'GREASEPENCIL':
            return False
        else:
            return True

    def addOperator(self, ctool, row, op, tool_icon, parms):
        ptool = parms.split('|')[1]
        
        C = bpy.context

        aref = ass_id = None

        if C.mode == 'PAINT_GREASE_PENCIL':
            aref = C.tool_settings.gpencil_paint.brush_asset_reference
        elif C.mode == 'SCULPT_GREASE_PENCIL':
            aref = C.tool_settings.gpencil_sculpt_paint.brush_asset_reference
        elif C.mode == 'VERTEX_GREASE_PENCIL':
            aref = C.tool_settings.gpencil_vertex_paint.brush_asset_reference
        elif C.mode == 'WEIGHT_GREASE_PENCIL':
            aref = C.tool_settings.gpencil_weight_paint.brush_asset_reference

        # aref.asset_library_type aref.asset_library_identifier
        if aref: ass_id = os.path.split(aref.relative_asset_identifier)[-1]
        
        isTool = False
        
        if ptool.split('.')[1] == ass_id:
            isTool = True
        else:
            isTool = (ctool == ptool)
            if C.mode != 'PAINT_GREASE_PENCIL' and ptool == 'builtin.brush':
                isTool = False
            
        row.operator(op, depress = isTool, icon = tool_icon).args = parms
        
    def draw(self, context):
        ctool = context.workspace.tools.from_space_view3d_mode(context.mode).idname
        mode = context.mode
        
        layout = self.layout

        if mode == 'VERTEX_GREASE_PENCIL':
            brush = context.tool_settings.gpencil_vertex_paint.brush
        else:
            brush = context.tool_settings.gpencil_paint.brush

        if brush:
            box = layout.box()
            row = box.row()
            row.template_color_picker(brush, "color", value_slider=True)
            row = box.row()
            col = row.column()
            if mode != 'OBJECT' and mode !=  'EDIT_GREASE_PENCIL' and mode !=  'SCULPT_GREASE_PENCIL':
                col.prop_with_popover(brush, "color", text="", panel="TOPBAR_PT_grease_pencil_vertex_color")
            else:
                col.label(text="Brush palette not available")
                col.enabled=False
            col = row.column()
            col.operator('quicktools.eyedropper', icon = 'EYEDROPPER', text = "")
        
        
        first_row = layout.row(align=True)
        col1 = first_row.column()
        box1 = col1.box()
        col2 = first_row.column()
        box2 = col2.box()
            
        row1 = box1.row()
        row1.operator(QuickToolsSetToolOperator.bl_idname, text = "OBJECT MODE").args = "OPS|OBJECT_MODE"
        row1 = box1.row()
        row1.operator(QuickToolsSetToolOperator.bl_idname, text = "UNDO").args = "OPS|UNDO"
        row1.operator(QuickToolsSetToolOperator.bl_idname, text = "REDO").args = "OPS|REDO"

        row1 = box1.row()
        row1.operator(QuickToolsSetToolOperator.bl_idname, text = "JOIN").args = "OPS|JOIN"
        row1.operator(QuickToolsSetToolOperator.bl_idname, text = "CLOSE").args = "OPS|CLOSE"
        row1.enabled = mode == 'EDIT_GREASE_PENCIL' or mode == 'SCULPT_GREASE_PENCIL' or mode == 'VERTEX_GREASE_PENCIL'
        
        row1 = box2.row(align=False)
        row1.operator(QuickToolsSetToolOperator.bl_idname, icon = "ZOOM_ALL").args = "OPS|VIEW_BOUNDS"
        row1.operator('quicktools.frame_selection', icon = "ZOOM_SELECTED", text="")
        row1.operator('quicktools.togglefullscreen', icon = "FULLSCREEN_ENTER", text="")
        row1.enabled = context.space_data.region_3d.view_perspective == 'CAMERA'

        row = box2.row()
        row.operator(QuickToolsSetToolOperator.bl_idname, icon = "SORT_DESC").args = "OPS|BRING_FORWARD"
        row.operator(QuickToolsSetToolOperator.bl_idname, icon = "EXPORT").args = "OPS|BRING_TO_FRONT"
        row.operator(QuickToolsSetToolOperator.bl_idname, text = "", icon = "CON_ACTION").args = "OPS|DISSOLVE"
        row.enabled = mode == 'EDIT_GREASE_PENCIL' or mode == 'SCULPT_GREASE_PENCIL' or mode == 'VERTEX_GREASE_PENCIL'
        row = box2.row()
        row.operator(QuickToolsSetToolOperator.bl_idname, icon = "SORT_ASC").args = "OPS|SEND_BACKWARD"
        row.operator(QuickToolsSetToolOperator.bl_idname, icon = "IMPORT").args = "OPS|SEND_TO_BACK"
        row.operator(QuickToolsSetToolOperator.bl_idname, text = "", icon = "X").args = "OPS|DELETE"
        row.enabled = mode == 'EDIT_GREASE_PENCIL' or mode == 'SCULPT_GREASE_PENCIL' or mode == 'VERTEX_GREASE_PENCIL'

        box = layout.box()
        row = box.row()
        row.label(text='EDIT TOOLS')
        
        row = box.row(align=True)
        self.addOperator(ctool, row, QuickToolsSetToolOperator.bl_idname, "RESTRICT_SELECT_OFF", "EDIT|builtin.select_box")
        row.separator()
        row.operator('quicktools.selectpoints', text="", icon = "PARTICLE_DATA")
        
        row.separator()

        selectmode = bpy.context.scene.tool_settings.gpencil_selectmode_edit
        row.operator(QuickToolsSetToolOperator.bl_idname, icon="GP_SELECT_POINTS", depress=selectmode=="POINT").args = "OPS|EDIT_POINT"
        row.operator(QuickToolsSetToolOperator.bl_idname, icon="GP_SELECT_STROKES", depress=selectmode=="STROKE").args = "OPS|EDIT_STROKE"
        row.separator()
        row.operator('quicktools.knifetool', icon = "SNAP_MIDPOINT", text = "" ) 
        row.separator()
        row.operator('quicktools.taper_thickness', icon="SPHERECURVE", text="")
        row.separator()
        row.operator("quicktools.submerge_strokes", icon="PARTICLE_POINT", text ="")

        row = box.row(align=True)
         
        self.addOperator(ctool, row, QuickToolsSetToolOperator.bl_idname, "ARROW_LEFTRIGHT", "EDIT|builtin.move")
        self.addOperator(ctool, row, QuickToolsSetToolOperator.bl_idname, "FILE_REFRESH", "EDIT|builtin.rotate")
        self.addOperator(ctool, row, QuickToolsSetToolOperator.bl_idname, "MOD_LENGTH", "EDIT|builtin.scale")
        row.separator()

        row.operator('quicktools.align_points', icon = 'ANCHOR_LEFT', text = '' ).align = 1
        row.operator('quicktools.align_points', icon = 'ANCHOR_TOP', text = '' ).align = 2
        
        row.separator()
        row.operator('quicktools.hardness', icon = 'MOD_OUTLINE', text = "")
        row.separator()
        row.operator(QuickToolsSetToolOperator.bl_idname, icon="VIEW_ORTHO").args = "OPS|SUBDIVIDE"
        row.separator()
        row.operator(QuickToolsSetToolOperator.bl_idname, icon="FCURVE").args = "OPS|SMOOTH"

        
        first_row = layout.row(align=True)
        box0 = first_row
        col1 = first_row.column(align=True)
        box1 = col1.box()
        col2 = first_row.column()
        box2 = col2.box()
        
        row = box1.row()
        row.label(text='DRAW TOOLS')
        row = box1.row(align=True)
        self.addOperator(ctool, row, QuickToolsSetToolOperator.bl_idname, "GREASEPENCIL", "PAINT_GREASE_PENCIL|builtin.brush")
        row.separator()
        self.addOperator(ctool, row, QuickToolsSetToolOperator.bl_idname, "IPO_LINEAR", "PAINT_GREASE_PENCIL|builtin.line")
        self.addOperator(ctool, row, QuickToolsSetToolOperator.bl_idname, "SPHERECURVE", "PAINT_GREASE_PENCIL|builtin.arc")
        self.addOperator(ctool, row, QuickToolsSetToolOperator.bl_idname, "MATPLANE", "PAINT_GREASE_PENCIL|builtin.box")
        row.separator()
        row.operator("quicktools.gptext", icon="EVENT_T", text ="")
        row.separator()
        self.addOperator(ctool, row, QuickToolsSetToolOperator.bl_idname, "LIBRARY_DATA_BROKEN", "PAINT_GREASE_PENCIL|builtin.trim")
        row = box1.row(align=True)
        self.addOperator(ctool, row, QuickToolsSetToolOperator.bl_idname, "FILTER", "PAINT_GREASE_PENCIL|builtin_brush.Fill")
        row.separator()
        self.addOperator(ctool, row, QuickToolsSetToolOperator.bl_idname, "IPO_CONSTANT", "PAINT_GREASE_PENCIL|builtin.polyline")
        self.addOperator(ctool, row, QuickToolsSetToolOperator.bl_idname, "IPO_EASE_OUT", "PAINT_GREASE_PENCIL|builtin.curve")
        self.addOperator(ctool, row, QuickToolsSetToolOperator.bl_idname, "ANTIALIASED", "PAINT_GREASE_PENCIL|builtin.circle")
        row.separator()
        row.operator("quicktools.snapigon", icon="SNAP_ON", text ="")
        row.separator()
        self.addOperator(ctool, row, QuickToolsSetToolOperator.bl_idname, "EVENT_TABLET_ERASER", "PAINT_GREASE_PENCIL|builtin_brush.Erase")
        
        row = box2.row()
        row.operator(QuickToolsSetToolOperator.bl_idname, text="", icon="SORTBYEXT", depress=context.tool_settings.use_gpencil_draw_additive).args = "OPS|DRAW_ADDITIVE"
        row = box2.row(align=True)
        onback = context.scene.tool_settings.use_gpencil_draw_onback
        row.operator(QuickToolsSetToolOperator.bl_idname, icon="SELECT_SUBTRACT", depress=onback==True).args = "OPS|DRAW_ONBACK"
        row = box2.row()
        mfe = context.tool_settings.use_grease_pencil_multi_frame_editing
        row.alert = mfe == True
        row.operator(QuickToolsSetToolOperator.bl_idname, icon="GP_MULTIFRAME_EDITING", depress=mfe==True).args = "OPS|MFE"
        
 
        first_row = layout.row(align=True)
        box0 = first_row
        col1 = first_row.column(align=True)
        box1 = col1.box()
        col2 = first_row.column()
        box2 = col2.box()

        row = box1.row()
        row.label(text='SCULPT TOOLS')
        row = box1.row(align=True)
        self.addOperator(ctool, row, QuickToolsSetToolOperator.bl_idname, "MOD_SMOOTH", "SCULPT_GREASE_PENCIL|builtin_brush.Smooth")
        self.addOperator(ctool, row, QuickToolsSetToolOperator.bl_idname, "MOD_THICKNESS", "SCULPT_GREASE_PENCIL|builtin_brush.Thickness")
        self.addOperator(ctool, row, QuickToolsSetToolOperator.bl_idname, "WPAINT_HLT", "SCULPT_GREASE_PENCIL|builtin_brush.Strength")
        row.separator()
        self.addOperator(ctool, row, QuickToolsSetToolOperator.bl_idname, "SHARPCURVE", "SCULPT_GREASE_PENCIL|builtin_brush.Pinch")
        self.addOperator(ctool, row, QuickToolsSetToolOperator.bl_idname, "FORCE_VORTEX", "SCULPT_GREASE_PENCIL|builtin_brush.Twist")
        self.addOperator(ctool, row, QuickToolsSetToolOperator.bl_idname, "MOD_NOISE", "SCULPT_GREASE_PENCIL|builtin_brush.Randomize")
        row = box1.row(align=True)
        self.addOperator(ctool, row, QuickToolsSetToolOperator.bl_idname, "VIEW_PAN", "SCULPT_GREASE_PENCIL|builtin_brush.Grab")
        self.addOperator(ctool, row, QuickToolsSetToolOperator.bl_idname, "SMOOTHCURVE", "SCULPT_GREASE_PENCIL|builtin_brush.Pull")
        row.separator()
        selectmode = bpy.context.scene.tool_settings.use_gpencil_select_mask_point
        row.operator(QuickToolsSetToolOperator.bl_idname, icon="GP_SELECT_POINTS", depress=selectmode).args = "OPS|SCULPT_POINT"
        selectmode = bpy.context.scene.tool_settings.use_gpencil_select_mask_stroke
        row.operator(QuickToolsSetToolOperator.bl_idname, icon="GP_SELECT_STROKES", depress=selectmode).args = "OPS|SCULPT_STROKE"
        row.separator()
        self.addOperator(ctool, row, QuickToolsSetToolOperator.bl_idname, "RESTRICT_SELECT_OFF", "SCULPT_GREASE_PENCIL|builtin.select_circle")
        
        row = box2.row()
        row.separator()
        row.scale_y = 8
        row = box2.row()
        row.operator('quicktools.interpolate_stroke', text="", icon = "AUTO")
        

        box = layout.box()
        row = box.row()
        row.label(text='PAINT COLOR TOOLS')
        row = box.row(align=True)
        self.addOperator(ctool, row, QuickToolsSetToolOperator.bl_idname, "BRUSH_DATA", "VERTEX_GREASE_PENCIL|builtin_brush.Paint")
        row.separator()
        self.addOperator(ctool, row, QuickToolsSetToolOperator.bl_idname, "MATFLUID", "VERTEX_GREASE_PENCIL|builtin_brush.Blur")
        self.addOperator(ctool, row, QuickToolsSetToolOperator.bl_idname, "ANTIALIASED", "VERTEX_GREASE_PENCIL|builtin_brush.Average")
        self.addOperator(ctool, row, QuickToolsSetToolOperator.bl_idname, "SEQ_LUMA_WAVEFORM", "VERTEX_GREASE_PENCIL|builtin_brush.Smear")
        row.separator()
        self.addOperator(ctool, row, QuickToolsSetToolOperator.bl_idname, "AREA_SWAP", "VERTEX_GREASE_PENCIL|builtin_brush.Replace")
        row.separator()
        row.operator(QuickToolsSetToolOperator.bl_idname, icon="SEQ_PREVIEW").args = "OPS|FILL"


        
# Class list to register
_classes = [
    quickFrameSelectionOperator,
    quickTaperStrokeOperator,
    quickAlignOperator,
    quickGPTextOperator,
    quickToggleFullScreenOperator,
    quickInterpolateStroke,
    quickHardnessOperator,
    quickSnapigonOperator,
    quickSubMergeOperator,
    quickEyeDropperOperator,
    QuickToolsSetToolOperator,
    QuickSelectPointsOperator,
    KnifeToolOperator,
    QuickToolsPanel,
]

def register():
    for cls in _classes:
        try:
            bpy.utils.register_class(cls)
        except:
            pass
        
def unregister():
    for cls in _classes:
        try:
            bpy.utils.unregister_class(cls)
        except:
            pass

if __name__ == "__main__":
    register()
