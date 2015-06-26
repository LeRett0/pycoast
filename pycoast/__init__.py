#!/usr/bin/env python
# -*- coding: utf-8 -*-
# pycoast, Writing of coastlines, borders and rivers to images in Python
#
# Copyright (C) 2011-2014
#    Esben S. Nielsen
#    Hróbjartur Þorsteinsson
#    Stefano Cerino
#    Katja Hungershofer
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
#(at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
import os
import numpy as np
from PIL import Image, ImageFont
from PIL import ImageDraw
import shapefile
import pyproj
from ConfigParser import ConfigParser, NoSectionError
import logging

LOGGER = logging.getLogger(__name__)


class ShapeFileError(Exception):

    """Class for error objects creation"""
    pass


class ContourWriterBase(object):

    """Base class for contourwriters. Do not instantiate.

    :Parameters:
    db_root_path : str
        Path to root dir of GSHHS and WDBII shapefiles
    """

    _draw_module = None
    # This is a flag to make _add_grid aware of which draw.text subroutine,
    # from PIL or from aggdraw is being used (unfortunately they are not fully
    # compatible).

    def __init__(self, db_root_path=None):
        if db_root_path is None:
            self.db_root_path = os.environ['GSHHS_DATA_ROOT']
        else:
            self.db_root_path = db_root_path

    def _draw_text(self, draw, position, txt, font, align='cc', **kwargs):
        """Draw text with agg module
        """
        txt_width, txt_height = draw.textsize(txt, font)
        x_pos, y_pos = position
        ax_, ay_ = align.lower()
        if ax_ == 'r':
            x_pos = x_pos - txt_width
        elif ax_ == 'c':
            x_pos = x_pos - txt_width / 2

        if ay_ == 'b':
            y_pos = y_pos - txt_height
        elif ay_ == 'c':
            y_pos = y_pos - txt_width / 2

        self._engine_text_draw(draw, (x_pos, y_pos), txt, font, **kwargs)

    def _engine_text_draw(self, draw, (x_pos, y_pos), txt, font, **kwargs):
        raise NotImplementedError('Text drawing undefined for render engine')

    def _draw_grid_labels(self, draw, xys, linetype, txt, font, **kwargs):
        """Draw text with default PIL module
        """
        placement_def = kwargs[linetype].lower()
        for xy_ in xys:
            # note xy[0] is xy coordinate pair,
            # xy[1] is required alignment e.g. 'tl','lr','lc','cc'...
            ax_, ay_ = xy_[1].lower()
            if ax_ in placement_def or ay_ in placement_def:
                self._draw_text(draw, xy_[0], txt, font, align=xy_[1], **kwargs)

    def _find_line_intercepts(self, xys, size, margins):
        """Finds intercepts of poly-line xys with image boundaries
        offset by margins and returns an array of coordintes"""
        x_size, y_size = size

        def is_in_box((x_loc, y_loc), (xmin, xmax, ymin, ymax)):
            if x_loc > xmin and x_loc < xmax and y_loc > ymin and y_loc < ymax:
                return True
            else:
                return False

        def crossing(x_1, x_2, lim):
            if (x_1 < lim) != (x_2 < lim):
                return True
            else:
                return False

        # set box limits
        xlim1 = margins[0]
        ylim1 = margins[1]
        xlim2 = x_size - margins[0]
        ylim2 = y_size - margins[0]

        # only consider crossing within a box a little bigger than grid
        # boundary
        search_box = (-10, x_size + 10, -10, y_size + 10)

        # loop trought line steps and detect crossings
        intercepts = []
        align_left = 'LC'
        align_right = 'RC'
        align_top = 'CT'
        align_bottom = 'CB'
        prev_xy = xys[0]
        for i in range(1, len(xys) - 1):
            xy_ = xys[i]
            if is_in_box(xy_, search_box):
                # crossing LHS
                if crossing(prev_xy[0], xy_[0], xlim1):
                    x_loc = xlim1
                    y_loc = xy_[1]
                    intercepts.append(((x_loc, y_loc), align_left))
                # crossing RHS
                elif crossing(prev_xy[0], xy_[0], xlim2):
                    x_loc = xlim2
                    y_loc = xy_[1]
                    intercepts.append(((x_loc, y_loc), align_right))
                # crossing Top
                elif crossing(prev_xy[1], xy_[1], ylim1):
                    x_loc = xy_[0]
                    y_loc = ylim1
                    intercepts.append(((x_loc, y_loc), align_top))
                # crossing Bottom
                elif crossing(prev_xy[1], xy_[1], ylim2):
                    x_loc = xy_[0]  # - txt_width/2
                    y_loc = ylim2  # - txt_height
                    intercepts.append(((x_loc, y_loc), align_bottom))
            prev_xy = xy_

        return intercepts

    def _add_grid(self, image, area_def,
                  lon_major, lat_major,
                  lon_minor, lat_minor,
                  font=None, write_text=True, **kwargs):
        """Add a lat lon grid to image
        """

        try:
            proj4_string = area_def.proj4_string
            area_extent = area_def.area_extent
        except AttributeError:
            proj4_string = area_def[0]
            area_extent = area_def[1]

        draw = self._get_canvas(image)

        is_agg = self._draw_module == "AGG"

        # use kwargs for major lines ... but reform for minor lines:
        minor_line_kwargs = kwargs.copy()
        minor_line_kwargs['outline'] = kwargs['minor_outline']
        if is_agg:
            minor_line_kwargs['outline_opacity'] = \
                kwargs['minor_outline_opacity']
            minor_line_kwargs['width'] = kwargs['minor_width']

        # set text fonts
        if font == None:
            font = ImageFont.load_default()
        # text margins (at sides of image frame)
        y_text_margin = 4
        x_text_margin = 4

        # Area and projection info
        x_size, y_size = image.size
        prj = pyproj.Proj(proj4_string)

        x_offset = 0
        y_offset = 0

        # Calculate min and max lons and lats of interest
        lon_min, lon_max, lat_min, lat_max = \
            _get_lon_lat_bounding_box(area_extent, x_size, y_size, prj)

        # Handle dateline crossing
        if lon_max < lon_min:
            lon_max = 360 + lon_max

        # Draw lonlat grid lines ...
        # create adjustment of line lengths to avoid cluttered pole lines
        if lat_max == 90.0:
            shorten_max_lat = lat_major
        else:
            shorten_max_lat = 0.0

        if lat_min == -90.0:
            increase_min_lat = lat_major
        else:
            increase_min_lat = 0.0

        # major lon lines
        round_lon_min = (lon_min - (lon_min % lon_major))
        maj_lons = np.arange(round_lon_min, lon_max, lon_major)
        maj_lons[maj_lons > 180] = maj_lons[maj_lons > 180] - 360

        # minor lon lines (ticks)
        min_lons = np.arange(round_lon_min, lon_max, lon_minor)
        min_lons[min_lons > 180] = min_lons[min_lons > 180] - 360

        # Get min_lons not in maj_lons
        min_lons = np.lib.arraysetops.setdiff1d(min_lons, maj_lons)

        # lats along major lon lines
        lin_lats = np.arange(lat_min + increase_min_lat,
                             lat_max - shorten_max_lat,
                             float(lat_max - lat_min) / y_size)
        # lin_lats in rather high definition so that it can be used to
        # posituion text labels near edges of image...

        # perhaps better to find the actual length of line in pixels...

        round_lat_min = (lat_min - (lat_min % lat_major))

        # major lat lines
        maj_lats = np.arange(round_lat_min + increase_min_lat,
                             lat_max, lat_major)

        # minor lon lines (ticks)
        min_lats = np.arange(round_lat_min + increase_min_lat,
                             lat_max - shorten_max_lat,
                             lat_minor)

        # Get min_lats not in maj_lats
        min_lats = np.lib.arraysetops.setdiff1d(min_lats, maj_lats)

        # lons along major lat lines (extended slightly to avoid missing the
        # end)
        lin_lons = np.arange(lon_min, lon_max + lon_major / 5.0,
                             lon_major / 10.0)

        # create dummpy shape object
        tmpshape = shapefile.Writer("")

        ##### MINOR LINES ######
        if not kwargs['minor_is_tick']:
            # minor lat lines
            for lat in min_lats:
                lonlats = [(x, lat) for x in lin_lons]
                tmpshape.points = lonlats
                index_arrays, is_reduced = _get_pixel_index(tmpshape,
                                                            area_extent,
                                                            x_size, y_size,
                                                            prj,
                                                            x_offset=x_offset,
                                                            y_offset=y_offset)
                del is_reduced
                # Skip empty datasets
                if len(index_arrays) == 0:
                    continue
                # make PIL draw the tick line...
                for index_array in index_arrays:
                    self._draw_line(draw,
                                    index_array.flatten().tolist(),
                                    **minor_line_kwargs)
            # minor lon lines
            for lon in min_lons:
                lonlats = [(lon, x) for x in lin_lats]
                tmpshape.points = lonlats
                index_arrays, is_reduced = _get_pixel_index(tmpshape,
                                                            area_extent,
                                                            x_size, y_size,
                                                            prj,
                                                            x_offset=x_offset,
                                                            y_offset=y_offset)
                # Skip empty datasets
                if len(index_arrays) == 0:
                    continue
                # make PIL draw the tick line...
                for index_array in index_arrays:
                    self._draw_line(draw,
                                    index_array.flatten().tolist(),
                                    **minor_line_kwargs)

        ##### MAJOR LINES AND MINOR TICKS ######
        # major lon lines and tick marks:
        for lon in maj_lons:
            # Draw 'minor' tick lines lat_minor separation along the lon
            if kwargs['minor_is_tick']:
                tick_lons = np.arange(lon - lon_major / 20.0,
                                      lon + lon_major / 20.0,
                                      lon_major / 50.0)

                for lat in min_lats:
                    lonlats = [(x, lat) for x in tick_lons]
                    tmpshape.points = lonlats
                    index_arrays, is_reduced = \
                        _get_pixel_index(tmpshape,
                                         area_extent,
                                         x_size, y_size,
                                         prj,
                                         x_offset=x_offset,
                                         y_offset=y_offset)
                    # Skip empty datasets
                    if len(index_arrays) == 0:
                        continue
                    # make PIL draw the tick line...
                    for index_array in index_arrays:
                        self._draw_line(draw,
                                        index_array.flatten().tolist(),
                                        **minor_line_kwargs)

            # Draw 'major' lines
            lonlats = [(lon, x) for x in lin_lats]
            tmpshape.points = lonlats
            index_arrays, is_reduced = _get_pixel_index(tmpshape, area_extent,
                                                        x_size, y_size,
                                                        prj,
                                                        x_offset=x_offset,
                                                        y_offset=y_offset)
            # Skip empty datasets
            if len(index_arrays) == 0:
                continue

            # make PIL draw the lines...
            for index_array in index_arrays:
                self._draw_line(draw,
                                index_array.flatten().tolist(),
                                **kwargs)

            # add lon text markings at each end of longitude line
            if write_text:
                if lon > 0.0:
                    txt = "%.2dE" % (lon)
                else:
                    txt = "%.2dW" % (-lon)
                xys = self._find_line_intercepts(index_array, image.size,
                                                 (x_text_margin, y_text_margin))

                self._draw_grid_labels(draw, xys, 'lon_placement',
                                       txt, font, **kwargs)

        # major lat lines and tick marks:
        for lat in maj_lats:
            # Draw 'minor' tick lon_minor separation along the lat
            if kwargs['minor_is_tick']:
                tick_lats = np.arange(lat - lat_major / 20.0,
                                      lat + lat_major / 20.0,
                                      lat_major / 50.0)
                for lon in min_lons:
                    lonlats = [(lon, x) for x in tick_lats]
                    tmpshape.points = lonlats
                    index_arrays, is_reduced = \
                        _get_pixel_index(tmpshape, area_extent,
                                         x_size, y_size,
                                         prj,
                                         x_offset=x_offset,
                                         y_offset=y_offset)
                    # Skip empty datasets
                    if len(index_arrays) == 0:
                        continue
                    # make PIL draw the tick line...
                    for index_array in index_arrays:
                        self._draw_line(draw,
                                        index_array.flatten().tolist(),
                                        **minor_line_kwargs)

            # Draw 'major' lines
            lonlats = [(x, lat) for x in lin_lons]
            tmpshape.points = lonlats
            index_arrays, is_reduced = _get_pixel_index(tmpshape, area_extent,
                                                        x_size, y_size,
                                                        prj,
                                                        x_offset=x_offset,
                                                        y_offset=y_offset)
            # Skip empty datasets
            if len(index_arrays) == 0:
                continue

            # make PIL draw the lines...
            for index_array in index_arrays:
                self._draw_line(draw, index_array.flatten().tolist(), **kwargs)

            # add lat text markings at each end of parallels ...
            if write_text:
                if lat >= 0.0:
                    txt = "%.2dN" % (lat)
                else:
                    txt = "%.2dS" % (-lat)
                xys = self._find_line_intercepts(index_array, image.size,
                                                 (x_text_margin, y_text_margin))
                self._draw_grid_labels(draw, xys, 'lat_placement',
                                       txt, font, **kwargs)

        # Draw cross on poles ...
        if lat_max == 90.0:
            crosslats = np.arange(90.0 - lat_major / 2.0, 90.0,
                                  float(lat_max - lat_min) / y_size)
            for lon in (0.0, 90.0, 180.0, -90.0):
                lonlats = [(lon, x) for x in crosslats]
                tmpshape.points = lonlats
                index_arrays, is_reduced = _get_pixel_index(tmpshape,
                                                            area_extent,
                                                            x_size, y_size,
                                                            prj,
                                                            x_offset=x_offset,
                                                            y_offset=y_offset)
                # Skip empty datasets
                if len(index_arrays) == 0:
                    continue

                # make PIL draw the lines...
                for index_array in index_arrays:
                    self._draw_line(draw,
                                    index_array.flatten().tolist(),
                                    **kwargs)
        if lat_min == -90.0:
            crosslats = np.arange(-90.0, -90.0 + lat_major / 2.0,
                                  float(lat_max - lat_min) / y_size)
            for lon in (0.0, 90.0, 180.0, -90.0):
                lonlats = [(lon, x) for x in crosslats]
                tmpshape.points = lonlats
                index_arrays, is_reduced = _get_pixel_index(tmpshape,
                                                            area_extent,
                                                            x_size, y_size,
                                                            prj,
                                                            x_offset=x_offset,
                                                            y_offset=y_offset)
                # Skip empty datasets
                if len(index_arrays) == 0:
                    continue

                # make PIL draw the lines...
                for index_array in index_arrays:
                    self._draw_line(draw,
                                    index_array.flatten().tolist(),
                                    **kwargs)
        self._finalize(draw)

    def _find_bounding_box(self, xys):
        lons = [x for (x, y) in xys]
        lats = [y for (x, y) in xys]
        return [min(lons), min(lats), max(lons), max(lats)]

    def _add_shapefile_shapes(self, image, area_def, filename,
                              feature_type=None, **kwargs):
        """ for drawing all shapes (polygon/poly-lines) from a custom shape
        file onto a PIL image
        """
        sf_ = shapefile.Reader(filename)
        for i in range(len(sf_.shapes())):
            self._add_shapefile_shape(image, area_def, filename, i,
                                      feature_type, **kwargs)

    def _add_shapefile_shape(self, image, area_def, filename, shape_id,
                             feature_type=None, **kwargs):
        """ for drawing a single shape (polygon/poly-line) definiton with id,
        shape_id from a custom shape file onto a PIL image
        """
        sf_ = shapefile.Reader(filename)
        shape = sf_.shape(shape_id)
        if feature_type is None:
            if shape.shapeType == 3:
                feature_type = "line"
            elif shape.shapeType == 5:
                feature_type = "polygon"
            else:
                raise ShapeFileError("Unsupported shape type: %s" % \
                                     str(shape.shapeType))

        self._add_shapes(image, area_def, feature_type, [shape], **kwargs)

    def _add_line(self, image, area_def, lonlats, **kwargs):
        """ For drawing a custom polyline. Lon and lat coordinates given by the
        list lonlat.
        """
        # create dummy shapelike object
        shape = type("", (), {})()
        shape.points = lonlats
        shape.parts = [0]
        shape.bbox = self._find_bounding_box(lonlats)
        self._add_shapes(image, area_def, "line", [shape], **kwargs)

    def _add_polygon(self, image, area_def, lonlats, **kwargs):
        """ For drawing a custom polygon. Lon and lat coordinates given by the
        list lonlat.
        """
        # create dummpy shapelike object
        shape = type("", (), {})()
        shape.points = lonlats
        shape.parts = [0]
        shape.bbox = self._find_bounding_box(lonlats)
        self._add_shapes(image, area_def, "polygon", [shape], **kwargs)

    def _add_shapes(self, image, area_def, feature_type, shapes,
                    x_offset=0, y_offset=0, **kwargs):
        """ For drawing shape objects to PIL image - better code reuse of
        drawing shapes - should be used in _add_feature and other methods of
        adding shapes including manually.
        """
        try:
            proj4_string = area_def.proj4_string
            area_extent = area_def.area_extent
        except AttributeError:
            proj4_string = area_def[0]
            area_extent = area_def[1]

        draw = self._get_canvas(image)

        # Area and projection info
        x_size, y_size = image.size
        prj = pyproj.Proj(proj4_string)

        # Calculate min and max lons and lats of interest
        lon_min, lon_max, lat_min, lat_max = \
            _get_lon_lat_bounding_box(area_extent, x_size, y_size, prj)

        # Iterate through shapes
        for i, shape in enumerate(shapes):
            # Check if polygon is possibly relevant
            s_lon_ll, s_lat_ll, s_lon_ur, s_lat_ur = shape.bbox
            if lon_min > lon_max:
                # Dateline crossing
                if ((s_lon_ur < lon_min and s_lon_ll > lon_max) or
                        lat_max < s_lat_ll or lat_min > s_lat_ur):
                    # Polygon is irrelevant
                    continue
            elif (lon_max < s_lon_ll or lon_min > s_lon_ur or
                  lat_max < s_lat_ll or lat_min > s_lat_ur):
                # Polygon is irrelevant
                continue

            # iterate over shape parts (some shapes split into parts)
            # dummy shape part object
            shape_part = type("", (), {})()
            parts = list(shape.parts) + [len(shape.points)]

            for i in range(len(parts) - 1):

                shape_part.points = shape.points[parts[i]:parts[i + 1]]

                # Get pixel index coordinates of shape
                index_arrays, is_reduced = _get_pixel_index(shape_part,
                                                            area_extent,
                                                            x_size, y_size,
                                                            prj,
                                                            x_offset=x_offset,
                                                            y_offset=y_offset)

                # Skip empty datasets
                if len(index_arrays) == 0:
                    continue

                # Make PIL draw the polygon or line
                for index_array in index_arrays:
                    if feature_type.lower() == 'polygon' and not is_reduced:
                        # Draw polygon if dataset has not been reduced
                        self._draw_polygon(draw,
                                           index_array.flatten().tolist(),
                                           **kwargs)
                    elif feature_type.lower() == 'line' or is_reduced:
                        # Draw line
                        self._draw_line(draw,
                                        index_array.flatten().tolist(),
                                        **kwargs)
                    else:
                        raise ValueError('Unknown contour type: %s'
                                         % feature_type)

        self._finalize(draw)

    def _add_feature(self, image, area_def, feature_type,
                     db_name, tag=None, zero_pad=False, resolution='c',
                     level=1, x_offset=0, y_offset=0, **kwargs):
        """Add a contour feature to image
        """

        try:
            proj4_string = area_def.proj4_string
            area_extent = area_def.area_extent
        except AttributeError:
            proj4_string = area_def[0]
            area_extent = area_def[1]

        draw = self._get_canvas(image)

        # Area and projection info
        x_size, y_size = image.size
        prj = pyproj.Proj(proj4_string)

        # Calculate min and max lons and lats of interest
        lon_min, lon_max, lat_min, lat_max = \
            _get_lon_lat_bounding_box(area_extent, x_size, y_size, prj)

        # Iterate through detail levels
        for shapes in self._iterate_db(db_name, tag, resolution,
                                       level, zero_pad):

            # Iterate through shapes
            for i, shape in enumerate(shapes):
                # Check if polygon is possibly relevant
                s_lon_ll, s_lat_ll, s_lon_ur, s_lat_ur = shape.bbox
                if lon_min > lon_max:
                    # Dateline crossing
                    if ((s_lon_ur < lon_min and s_lon_ll > lon_max) or
                            lat_max < s_lat_ll or lat_min > s_lat_ur):
                        # Polygon is irrelevant
                        continue
                elif (lon_max < s_lon_ll or lon_min > s_lon_ur or
                      lat_max < s_lat_ll or lat_min > s_lat_ur):
                    # Polygon is irrelevant
                    continue

                # Get pixel index coordinates of shape

                index_arrays, is_reduced = _get_pixel_index(shape,
                                                            area_extent,
                                                            x_size, y_size,
                                                            prj,
                                                            x_offset=x_offset,
                                                            y_offset=y_offset)

                # Skip empty datasets
                if len(index_arrays) == 0:
                    continue

                # Make PIL draw the polygon or line
                for index_array in index_arrays:
                    if feature_type.lower() == 'polygon' and not is_reduced:
                        # Draw polygon if dataset has not been reduced
                        self._draw_polygon(draw,
                                           index_array.flatten().tolist(),
                                           **kwargs)
                    elif feature_type.lower() == 'line' or is_reduced:
                        # Draw line
                        self._draw_line(draw,
                                        index_array.flatten().tolist(),
                                        **kwargs)
                    else:
                        raise ValueError('Unknown contour type: %s'
                                         % feature_type)

        self._finalize(draw)

    def _iterate_db(self, db_name, tag, resolution, level, zero_pad):
        """Iterate trough datasets
        """

        format_string = '%s_%s_'
        if tag is not None:
            format_string += '%s_'

        if zero_pad:
            format_string += 'L%02i.shp'
        else:
            format_string += 'L%s.shp'

        for i in range(level):

            # One shapefile per level
            if tag is None:
                shapefilename = \
                    os.path.join(self.db_root_path, '%s_shp' % db_name,
                                 resolution, format_string %
                                 (db_name, resolution, (i + 1)))
            else:
                shapefilename = \
                    os.path.join(self.db_root_path, '%s_shp' % db_name,
                                 resolution, format_string %
                                 (db_name, tag, resolution, (i + 1)))
            try:
                sf_ = shapefile.Reader(shapefilename)
                shapes = sf_.shapes()
            except AttributeError:
                raise ShapeFileError('Could not find shapefile %s' % \
                                     shapefilename)
            yield shapes

    def _finalize(self, draw):
        """Do any need finalization of the drawing
        """

        pass

    def add_overlay_from_config(self, config_file, area_def):
        """Create and return a transparent image adding all the
           overlays contained in a configuration file.

        :Parameters:
        config_file : str
            Configuration file name
        area_def : object
            Area Definition of the creating image
        """

        config = ConfigParser()
        try:
            with open(config_file, 'r'):
                LOGGER.info("Overlays config file %s found",
                            str(config_file))
            config.read(config_file)
        except IOError:
            LOGGER.error("Overlays config file %s does not exist!",
                         str(config_file))
            raise
        except NoSectionError:
            LOGGER.error("Error in %s", str(config_file))
            raise

        def parse_color_tuple(string):
            '''Parse color tuple from string.'''
            if '(' in string:
                string = string.replace('(', '').replace(')')
                string = tuple([int(i) for i in string.split(',')])
            return string

        # Cache management
        cache_file = None
        if config.has_section('cache'):
            cache_file = (config.get('cache', 'file') + '_'
                          + area_def.area_id + '.png')

            try:
                config_time = os.path.getmtime(config_file)
                cache_time = os.path.getmtime(cache_file)
                # Cache file will be used only if it's newer than config file
                if config_time < cache_time:
                    foreground = Image.open(cache_file)
                    LOGGER.info('Using image in cache %s', cache_file)
                    return foreground
                else:
                    LOGGER.info("Cache file is not used "
                                "because config file has changed")
            except OSError:
                LOGGER.info("New overlay image will be saved in cache")

        x_size = area_def.x_size
        y_size = area_def.y_size
        foreground = Image.new('RGBA', (x_size, y_size), (0, 0, 0, 0))

        # Lines (coasts, rivers, borders) management
        x_resolution = ((area_def.area_extent[2] -
                         area_def.area_extent[0]) /
                        x_size)
        y_resolution = ((area_def.area_extent[3] -
                         area_def.area_extent[1]) /
                        y_size)
        res = min(x_resolution, y_resolution)

        if res > 25000:
            default_resolution = "c"
        elif res > 5000:
            default_resolution = "l"
        elif res > 1000:
            default_resolution = "i"
        elif res > 200:
            default_resolution = "h"
        else:
            default_resolution = "f"

        defaults = {'level': 1,
                    'outline': 'white',
                    'width': 1,
                    'fill': None,
                    'fill_opacity': 255,
                    'outline_opacity': 255,
                    'x_offset': 0,
                    'y_offset': 0,
                    'resolution': default_resolution}

        sections = ['coasts', 'rivers', 'borders', 'cities', 'grid']
        overlays = {}

        for section in config.sections():
            if section in sections:
                overlays[section] = {}
                for option in config.options(section):
                    # check for color tuple()s
                    if option in ['outline', 'fill', 'minor_outline']:
                        overlays[section][option] = \
                            parse_color_tuple(config.get(section, option))
                    else:
                        overlays[section][option] = config.get(section, option)

        is_agg = self._draw_module == "AGG"

        # Coasts
        for section, fun in zip(['coasts', 'rivers', 'borders'],
                                [self.add_coastlines,
                                 self.add_rivers,
                                 self.add_borders]):

            if overlays.has_key(section):

                params = defaults.copy()
                params.update(overlays[section])

                params['level'] = int(params['level'])
                params['x_offset'] = float(params['x_offset'])
                params['y_offset'] = float(params['y_offset'])
                params['width'] = float(params['width'])
                params['outline_opacity'] = int(params['outline_opacity'])
                params['fill_opacity'] = int(params['fill_opacity'])

                if section != "coasts":
                    params.pop('fill_opacity', None)
                    params.pop('fill', None)

                if not is_agg:
                    for key in ['width', 'outline_opacity', 'fill_opacity']:
                        params.pop(key, None)

                fun(foreground, area_def, **params)
                LOGGER.info("%s added", section.capitalize())

        # Cities management
        if overlays.has_key('cities'):

            citylist = [s.lstrip()
                        for s in overlays['cities']['list'].split(',')]
            font_file = overlays['cities']['font']
            font_size = int(overlays['cities'].get('font_size',
                                                   12))
            outline = parse_color_tuple(overlays['cities'].get('outline',
                                                               'yellow'))
            pt_size = int(overlays['cities'].get('pt_size', None))
            box_outline = overlays['cities'].get('box_outline', None)
            box_opacity = int(overlays['cities'].get('box_opacity', 255))

            self.add_cities(foreground, area_def, citylist, font_file,
                            font_size, pt_size, outline, box_outline,
                            box_opacity)

        if 'grid' in overlays:
            lon_major = float(overlays['grid'].get('lon_major', 10.0))
            lat_major = float(overlays['grid'].get('lat_major', 10.0))
            lon_minor = float(overlays['grid'].get('lon_minor', 2.0))
            lat_minor = float(overlays['grid'].get('lat_minor', 2.0))
            font = overlays['grid'].get('font', None)
            write_text = overlays['grid'].get('write_text',
                                              'true').lower() in \
                ['true', 'yes', '1']
            fill = overlays['grid'].get('fill', None)
            outline = parse_color_tuple(overlays['grid'].get('outline',
                                                             'white'))
            minor_outline = \
                parse_color_tuple(overlays['grid'].get('minor_outline',
                                                       'white'))
            minor_is_tick = overlays['grid'].get('minor_is_tick',
                                                 'true').lower() in \
                ['true', 'yes', '1']
            lon_placement = overlays['grid'].get('lon_placement', 'tb')
            lat_placement = overlays['grid'].get('lat_placement', 'lr')

            self.add_grid(foreground, area_def, (lon_major, lat_major),
                          (lon_minor, lat_minor),
                          font=font, write_text=write_text, fill=fill,
                          outline=outline, minor_outline=minor_outline,
                          minor_is_tick=minor_is_tick,
                          lon_placement=lon_placement,
                          lat_placement=lat_placement)

        if cache_file is not None:
            try:
                foreground.save(cache_file)
            except IOError as err:
                LOGGER.error("Can't save cache: %s", str(err))

        return foreground

    def add_cities(self, image, area_def, citylist, font_file, font_size,
                   ptsize, outline, box_outline, box_opacity):
        """Add cities (point and name) to a PIL image object

        """

        try:
            proj4_string = area_def.proj4_string
            area_extent = area_def.area_extent
        except AttributeError:
            proj4_string = area_def[0]
            area_extent = area_def[1]

        draw = self._get_canvas(image)

        # Area and projection info
        x_size, _ = image.size
        # prj = pyproj.Proj(proj4_string)

        # read shape file with points
        # Sc-Kh shapefilename = os.path.join(self.db_root_path,
        # "cities_15000_alternativ.shp")
        shapefilename = os.path.join(
            self.db_root_path, os.path.join("CITIES",
                                            "cities_15000_alternativ.shp"))
        try:
            sf_ = shapefile.Reader(shapefilename)
            shapes = sf_.shapes()
        except AttributeError:
            raise ShapeFileError('Could not find shapefile %s' % \
                                 shapefilename)

        is_agg = self._draw_module == "AGG"
        if is_agg:
            import aggdraw
            font = aggdraw.Font(outline, font_file, size=font_size)
        else:
            font = ImageFont.truetype(font_file, font_size)

        # Iterate through shapes
        for i, shape in enumerate(shapes):
            # Select cities with name
            record = sf_.record(i)
            if record[3] in citylist:

                city_name = record[3]

                # use only parts of _get_pixel_index
                # Get shape data as array and reproject
                shape_data = np.array(shape.points)
                lons = shape_data[:, 0][0]
                lats = shape_data[:, 1][0]

                try:
                    (x_loc, y_loc) = area_def.get_xy_from_lonlat(lons, lats)
                except ValueError, exc:
                    LOGGER.debug("Point not added (%s)", str(exc))
                else:

                    # add_dot
                    if ptsize is not None:
                        dot_box = [x_loc - ptsize, y_loc - ptsize,
                                   x_loc + ptsize, y_loc + ptsize]
                        self._draw_ellipse(draw, dot_box, fill=outline,
                                           outline=outline)
                        text_position = [x_loc + 9, y_loc - 5]  # FIX ME
                    else:
                        text_position = [x_loc, y_loc]

                    # add text_box
                    self._draw_text_box(draw, text_position, city_name,
                                        font, outline, box_outline,
                                        box_opacity)
                    LOGGER.info("%s added", str(city_name))

        self._finalize(draw)


class ContourWriter(ContourWriterBase):

    """Adds countours from GSHHS and WDBII to images

    :Parameters:
    db_root_path : str
        Path to root dir of GSHHS and WDBII shapefiles
    """

    _draw_module = "PIL"
    # This is a flag to make _add_grid aware
    # of which text draw routine from PIL or
    # from aggdraw should be used (unfortunately
    # they are not fully compatible)

    def _get_canvas(self, image):
        """Returns PIL image object
        """

        return ImageDraw.Draw(image)

    def _engine_text_draw(self, draw, (x_pos, y_pos), txt, font, **kwargs):
        draw.text((x_pos, y_pos), txt, font=font, fill=kwargs['fill'])

    def _draw_polygon(self, draw, coordinates, **kwargs):
        """Draw polygon
        """

        draw.polygon(coordinates, fill=kwargs['fill'],
                     outline=kwargs['outline'])

    def _draw_ellipse(self, draw, coordinates, **kwargs):
        """Draw ellipse 
        """
        draw.ellipse(coordinates, fill=kwargs['fill'],
                     outline=kwargs['outline'])

    def _draw_rectangle(self, draw, coordinates, **kwargs):
        """Draw rectangle 
        """
        draw.rectangle(coordinates, fill=kwargs['fill'],
                       outline=kwargs['outline'])

    def _draw_text_box(self, draw, text_position, text, font, outline,
                       box_outline, box_opacity):
        """Add text box in xy
        """

        if box_outline is not None:
            LOGGER.warning("Box background will not added; "
                           "please install aggdraw lib")

        self._draw_text(draw, text_position, text, font, align="no",
                        fill=outline)

    def _draw_line(self, draw, coordinates, **kwargs):
        """Draw line
        """

        draw.line(coordinates, fill=kwargs['outline'])

    def add_shapefile_shapes(self, image, area_def, filename, feature_type=None,
                             fill=None, outline='white',
                             x_offset=0, y_offset=0):
        """Add shape file shapes from an ESRI shapefile.
        Note: Currently only supports lon-lat formatted coordinates.

        :Parameters:
        image : object
            PIL image object
        area_def : list [proj4_string, area_extent]
          | proj4_string : str
          |     Projection of area as Proj.4 string
          | area_extent : list
          |     Area extent as a list (LL_x, LL_y, UR_x, UR_y)
        filename : str
            Path to ESRI shape file
        feature_type : 'polygon' or 'line',
            only to override the shape type defined in shapefile, optional
        fill : str or (R, G, B), optional
            Polygon fill color
        fill_opacity : int, optional {0; 255}
            Opacity of polygon fill
        outline : str or (R, G, B), optional
            line color
        outline_opacity : int, optional {0; 255}
            Opacity of lines
        x_offset : float, optional
            Pixel offset in x direction
        y_offset : float, optional
            Pixel offset in y direction
        """
        self._add_shapefile_shapes(image=image, area_def=area_def,
                                   filename=filename, feature_type=feature_type,
                                   x_offset=x_offset, y_offset=y_offset,
                                   fill=fill,
                                   outline=outline)

    def add_shapefile_shape(self, image, area_def, filename, shape_id,
                            feature_type=None,
                            fill=None, outline='white',
                            x_offset=0, y_offset=0):
        """Add a single shape file shape from an ESRI shapefile.
        Note: To add all shapes in file use the 'add_shape_file_shapes' routine.
        Note: Currently only supports lon-lat formatted coordinates.

        :Parameters:
        image : object
            PIL image object
        area_def : list [proj4_string, area_extent]
          | proj4_string : str
          |     Projection of area as Proj.4 string
          | area_extent : list
          |     Area extent as a list (LL_x, LL_y, UR_x, UR_y)
        filename : str
            Path to ESRI shape file
        shape_id : int
            integer id of shape in shape file {0; ... }
        feature_type : 'polygon' or 'line',
            only to override the shape type defined in shapefile, optional
        fill : str or (R, G, B), optional
            Polygon fill color
        outline : str or (R, G, B), optional
            line color
        x_offset : float, optional
            Pixel offset in x direction
        y_offset : float, optional
            Pixel offset in y direction
        """
        self._add_shapefile_shape(image=image,
                                  area_def=area_def, filename=filename,
                                  shape_id=shape_id,
                                  feature_type=feature_type,
                                  x_offset=x_offset, y_offset=y_offset,
                                  fill=fill,
                                  outline=outline)

    def add_line(self, image, area_def, lonlats,
                 fill=None, outline='white', x_offset=0, y_offset=0):
        """Add a user defined poly-line from a list of (lon,lat) coordinates.

        :Parameters:
        image : object
            PIL image object
        area_def : list [proj4_string, area_extent]
          | proj4_string : str
          |     Projection of area as Proj.4 string
          | area_extent : list
          |     Area extent as a list (LL_x, LL_y, UR_x, UR_y)
        lonlats : list of lon lat pairs
            e.g. [(10,20),(20,30),...,(20,20)]
        outline : str or (R, G, B), optional
            line color
        width : float, optional
            line width
        x_offset : float, optional
            Pixel offset in x direction
        y_offset : float, optional
            Pixel offset in y direction
        """
        self._add_line(image, area_def, lonlats,
                       x_offset=x_offset, y_offset=y_offset,
                       fill=fill, outline=outline)

    def add_polygon(self, image, area_def, lonlats,
                    fill=None, outline='white', x_offset=0, y_offset=0):
        """Add a user defined polygon from a list of (lon,lat) coordinates.

        :Parameters:
        image : object
            PIL image object
        area_def : list [proj4_string, area_extent]
          | proj4_string : str
          |     Projection of area as Proj.4 string
          | area_extent : list
          |     Area extent as a list (LL_x, LL_y, UR_x, UR_y)
        lonlats : list of lon lat pairs
            e.g. [(10,20),(20,30),...,(20,20)]
        fill : str or (R, G, B), optional
            Polygon fill color
        outline : str or (R, G, B), optional
            line color
        x_offset : float, optional
            Pixel offset in x direction
        y_offset : float, optional
            Pixel offset in y direction
        """
        self._add_polygon(image, area_def, lonlats,
                          x_offset=x_offset, y_offset=y_offset,
                          fill=fill, outline=outline)

    def add_grid(self, image, area_def, (lon_major, lat_major),
                 (lon_minor, lat_minor),
                 font=None, write_text=True, fill=None, outline='white',
                 minor_outline='white', minor_is_tick=True,
                 lon_placement='tb', lat_placement='lr'):
        """Add a lon-lat grid to a PIL image object

        :Parameters:
        image : object
            PIL image object
        proj4_string : str
            Projection of area as Proj.4 string
        (lon_major,lat_major): (float,float)
            Major grid line separation
        (lon_minor,lat_minor): (float,float)
            Minor grid line separation
        font: PIL ImageFont object, optional
            Font for major line markings
        write_text : boolean, optional
            Deterine if line markings are enabled
        fill : str or (R, G, B), optional
            Text color
        outline : str or (R, G, B), optional
            Major line color
        minor_outline : str or (R, G, B), optional
            Minor line/tick color
        minor_is_tick : boolean, optional
            Use tick minor line style (True) or full minor line style (False)
        """
        self._add_grid(image, area_def, lon_major, lat_major,
                       lon_minor, lat_minor, font=font,
                       write_text=write_text, fill=fill, outline=outline,
                       minor_outline=minor_outline, minor_is_tick=minor_is_tick,
                       lon_placement=lon_placement, lat_placement=lat_placement)

    def add_grid_to_file(self, filename, area_def, (lon_major, lat_major),
                         (lon_minor, lat_minor),
                         font=None, write_text=True, fill=None, outline='white',
                         minor_outline='white', minor_is_tick=True,
                         lon_placement='tb', lat_placement='lr'):
        """Add a lon-lat grid to an image file

        :Parameters:
        image : object
            PIL image object
        proj4_string : str
            Projection of area as Proj.4 string
        (lon_major,lat_major): (float,float)
            Major grid line separation
        (lon_minor,lat_minor): (float,float)
            Minor grid line separation
        font: PIL ImageFont object, optional
            Font for major line markings
        write_text : boolean, optional
            Deterine if line markings are enabled
        fill : str or (R, G, B), optional
            Text color
        outline : str or (R, G, B), optional
            Major line color
        minor_outline : str or (R, G, B), optional
            Minor line/tick color
        minor_is_tick : boolean, optional
            Use tick minor line style (True) or full minor line style (False)
        """

        image = Image.open(filename)
        self.add_grid(image, area_def, (lon_major, lat_major),
                      (lon_minor, lat_minor), font=font,
                      write_text=write_text, fill=fill, outline=outline,
                      minor_outline=minor_outline,
                      minor_is_tick=minor_is_tick,
                      lon_placement=lon_placement, lat_placement=lat_placement)
        image.save(filename)

    def add_coastlines(self, image, area_def, resolution='c', level=1,
                       fill=None, outline='white', x_offset=0, y_offset=0):
        """Add coastlines to a PIL image object

        :Parameters:
        image : object
            PIL image object
        proj4_string : str
            Projection of area as Proj.4 string
        area_extent : list
            Area extent as a list (LL_x, LL_y, UR_x, UR_y)
        resolution : str, optional {'c', 'l', 'i', 'h', 'f'}
            Dataset resolution to use
        level : int, optional {1, 2, 3, 4}
            Detail level of dataset
        fill : str or (R, G, B), optional
            Land color
        outline : str or (R, G, B), optional
            Coastline color
        x_offset : float, optional
            Pixel offset in x direction
        y_offset : float, optional
            Pixel offset in y direction
        """

        self._add_feature(image, area_def, 'polygon', 'GSHHS',
                          resolution=resolution, level=level,
                          fill=fill, outline=outline, x_offset=x_offset,
                          y_offset=y_offset)

    def add_coastlines_to_file(self, filename, area_def, resolution='c',
                               level=1, fill=None, outline='white',
                               x_offset=0, y_offset=0):
        """Add coastlines to an image file

        :Parameters:
        filename : str
            Image file
        proj4_string : str
            Projection of area as Proj.4 string
        area_extent : list
            Area extent as a list (LL_x, LL_y, UR_x, UR_y)
        resolution : str, optional {'c', 'l', 'i', 'h', 'f'}
            Dataset resolution to use
        level : int, optional {1, 2, 3, 4}
            Detail level of dataset
        fill : str or (R, G, B)
            Land color
        outline : str or (R, G, B), optional
            Coastline color
        x_offset : float, optional
            Pixel offset in x direction
        y_offset : float, optional
            Pixel offset in y direction
        """

        image = Image.open(filename)
        self.add_coastlines(image, area_def,
                            resolution=resolution, level=level,
                            fill=fill, outline=outline, x_offset=x_offset,
                            y_offset=y_offset)
        image.save(filename)

    def add_borders(self, image, area_def, resolution='c', level=1,
                    outline='white', x_offset=0, y_offset=0):
        """Add borders to a PIL image object

        :Parameters:
        image : object
            PIL image object
        proj4_string : str
            Projection of area as Proj.4 string
        area_extent : list
            Area extent as a list (LL_x, LL_y, UR_x, UR_y)
        resolution : str, optional {'c', 'l', 'i', 'h', 'f'}
            Dataset resolution to use
        level : int, optional {1, 2, 3}
            Detail level of dataset
        outline : str or (R, G, B), optional
            Border color
        x_offset : float, optional
            Pixel offset in x direction
        y_offset : float, optional
            Pixel offset in y direction
        """

        self._add_feature(image, area_def, 'line', 'WDBII',
                          tag='border', resolution=resolution, level=level,
                          outline=outline, x_offset=x_offset,
                          y_offset=y_offset)

    def add_borders_to_file(self, filename, area_def, resolution='c', level=1,
                            outline='white', x_offset=0, y_offset=0):
        """Add borders to an image file

        :Parameters:
        image : object
            Image file
        proj4_string : str
            Projection of area as Proj.4 string
        area_extent : list
            Area extent as a list (LL_x, LL_y, UR_x, UR_y)
        resolution : str, optional {'c', 'l', 'i', 'h', 'f'}
            Dataset resolution to use
        level : int, optional {1, 2, 3}
            Detail level of dataset
        outline : str or (R, G, B), optional
            Border color
        x_offset : float, optional
            Pixel offset in x direction
        y_offset : float, optional
            Pixel offset in y direction
        """
        image = Image.open(filename)
        self.add_borders(image, area_def, resolution=resolution,
                         level=level, outline=outline, x_offset=x_offset,
                         y_offset=y_offset)
        image.save(filename)

    def add_rivers(self, image, area_def, resolution='c', level=1,
                   outline='white', x_offset=0, y_offset=0):
        """Add rivers to a PIL image object

        :Parameters:
        image : object
            PIL image object
        proj4_string : str
            Projection of area as Proj.4 string
        area_extent : list
            Area extent as a list (LL_x, LL_y, UR_x, UR_y)
        resolution : str, optional {'c', 'l', 'i', 'h', 'f'}
            Dataset resolution to use
        level : int, optional {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11}
            Detail level of dataset
        outline : str or (R, G, B), optional
            River color
        x_offset : float, optional
            Pixel offset in x direction
        y_offset : float, optional
            Pixel offset in y direction
        """

        self._add_feature(image, area_def, 'line', 'WDBII',
                          tag='river', zero_pad=True, resolution=resolution,
                          level=level, outline=outline, x_offset=x_offset,
                          y_offset=y_offset)

    def add_rivers_to_file(self, filename, area_def, resolution='c', level=1,
                           outline='white', x_offset=0, y_offset=0):
        """Add rivers to an image file

        :Parameters:
        image : object
            Image file
        proj4_string : str
            Projection of area as Proj.4 string
        area_extent : list
            Area extent as a list (LL_x, LL_y, UR_x, UR_y)
        resolution : str, optional {'c', 'l', 'i', 'h', 'f'}
            Dataset resolution to use
        level : int, optional {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11}
            Detail level of dataset
        outline : str or (R, G, B), optional
            River color
        x_offset : float, optional
            Pixel offset in x direction
        y_offset : float, optional
            Pixel offset in y direction
        """

        image = Image.open(filename)
        self.add_rivers(image, area_def, resolution=resolution, level=level,
                        outline=outline, x_offset=x_offset, y_offset=y_offset)
        image.save(filename)


class ContourWriterAGG(ContourWriterBase):

    """Adds countours from GSHHS and WDBII to images
       using the AGG engine for high quality images.

    :Parameters:
    db_root_path : str
        Path to root dir of GSHHS and WDBII shapefiles
    """
    _draw_module = "AGG"
    # This is a flag to make _add_grid aware of which text draw routine
    # from PIL or from aggdraw should be used
    # (unfortunately they are not fully compatible)

    def _get_canvas(self, image):
        """Returns AGG image object
        """

        import aggdraw
        return aggdraw.Draw(image)

    def _engine_text_draw(self, draw, (x_pos, y_pos), txt, font, **kwargs):
        draw.text((x_pos, y_pos), txt, font)

    def _draw_polygon(self, draw, coordinates, **kwargs):
        """Draw polygon
        """

        import aggdraw
        pen = aggdraw.Pen(kwargs['outline'],
                          kwargs['width'],
                          kwargs['outline_opacity'])
        if kwargs['fill'] is None:
            fill_opacity = 0
        else:
            fill_opacity = kwargs['fill_opacity']
        brush = aggdraw.Brush(kwargs['fill'], fill_opacity)
        draw.polygon(coordinates, pen, brush)

    def _draw_rectangle(self, draw, coordinates, **kwargs):
        """Draw rectangle
        """

        import aggdraw
        pen = aggdraw.Pen(kwargs['outline'])

        fill_opacity = kwargs.get('fill_opacity', 255)
        brush = aggdraw.Brush(kwargs['fill'], fill_opacity)
        draw.rectangle(coordinates, pen, brush)

    def _draw_ellipse(self, draw, coordinates, **kwargs):
        """Draw ellipse
        """

        import aggdraw
        pen = aggdraw.Pen(kwargs['outline'])

        fill_opacity = kwargs.get('fill_opacity', 255)
        brush = aggdraw.Brush(kwargs['fill'], fill_opacity)
        draw.ellipse(coordinates, brush, pen)

    def _draw_text_box(self, draw, text_position, text, font,
                       outline, box_outline, box_opacity):
        """Add text box in xy
        """

        if box_outline is not None:
            text_size = draw.textsize(text, font)
            margin = 2
            xUL = text_position[0] - margin
            yUL = text_position[1]
            xLR = xUL + text_size[0] + (2 * margin)
            yLR = yUL + text_size[1]
            box_size = (xUL, yUL, xLR, yLR)

            self._draw_rectangle(draw, box_size, fill=box_outline,
                                 fill_opacity=box_opacity,
                                 outline=box_outline)

        self._draw_text(draw, text_position, text, font, align="no")

    def _draw_line(self, draw, coordinates, **kwargs):
        """Draw line
        """

        import aggdraw
        pen = aggdraw.Pen(kwargs['outline'],
                          kwargs['width'],
                          kwargs['outline_opacity'])
        draw.line(coordinates, pen)

    def _finalize(self, draw):
        """Flush the AGG image object
        """

        draw.flush()

    def add_shapefile_shapes(self, image, area_def, filename,
                             feature_type=None, fill=None, fill_opacity=255,
                             outline='white', width=1,
                             outline_opacity=255, x_offset=0, y_offset=0):
        """Add shape file shapes from an ESRI shapefile.
        Note: Currently only supports lon-lat formatted coordinates.

        :Parameters:
        image : object
            PIL image object
        area_def : list [proj4_string, area_extent]
          | proj4_string : str
          |     Projection of area as Proj.4 string
          | area_extent : list
          |     Area extent as a list (LL_x, LL_y, UR_x, UR_y)
        filename : str
            Path to ESRI shape file
        feature_type : 'polygon' or 'line',
            only to override the shape type defined in shapefile, optional
        fill : str or (R, G, B), optional
            Polygon fill color
        fill_opacity : int, optional {0; 255}
            Opacity of polygon fill
        outline : str or (R, G, B), optional
            line color
        width : float, optional
            line width
        outline_opacity : int, optional {0; 255}
            Opacity of lines
        x_offset : float, optional
            Pixel offset in x direction
        y_offset : float, optional
            Pixel offset in y direction
        """
        self._add_shapefile_shapes(image=image, area_def=area_def,
                                   filename=filename, feature_type=feature_type,
                                   x_offset=x_offset, y_offset=y_offset,
                                   fill=fill, fill_opacity=fill_opacity,
                                   outline=outline, width=width,
                                   outline_opacity=outline_opacity)

    def add_shapefile_shape(self, image, area_def, filename, shape_id,
                            feature_type=None, fill=None,
                            fill_opacity=255, outline='white', width=1,
                            outline_opacity=255, x_offset=0, y_offset=0):
        """Add a single shape file shape from an ESRI shapefile.
        Note: To add all shapes in file use the 'add_shape_file_shapes' routine.
        Note: Currently only supports lon-lat formatted coordinates.

        :Parameters:
        image : object
            PIL image object
        area_def : list [proj4_string, area_extent]
          | proj4_string : str
          |     Projection of area as Proj.4 string
          | area_extent : list
          |     Area extent as a list (LL_x, LL_y, UR_x, UR_y)
        filename : str
            Path to ESRI shape file
        shape_id : int
            integer id of shape in shape file {0; ... }
        feature_type : 'polygon' or 'line',
            only to override the shape type defined in shapefile, optional
        fill : str or (R, G, B), optional
            Polygon fill color
        fill_opacity : int, optional {0; 255}
            Opacity of polygon fill
        outline : str or (R, G, B), optional
            line color
        width : float, optional
            line width
        outline_opacity : int, optional {0; 255}
            Opacity of lines
        x_offset : float, optional
            Pixel offset in x direction
        y_offset : float, optional
            Pixel offset in y direction
        """
        self._add_shapefile_shape(image=image,
                                  area_def=area_def, filename=filename,
                                  shape_id=shape_id,
                                  feature_type=feature_type,
                                  x_offset=x_offset, y_offset=y_offset,
                                  fill=fill, fill_opacity=fill_opacity,
                                  outline=outline, width=width,
                                  outline_opacity=outline_opacity)

    def add_line(self, image, area_def, lonlats,
                 fill=None, fill_opacity=255, outline='white', width=1,
                 outline_opacity=255, x_offset=0, y_offset=0):
        """Add a user defined poly-line from a list of (lon,lat) coordinates.

        :Parameters:
        image : object
            PIL image object
        area_def : list [proj4_string, area_extent]
          | proj4_string : str
          |     Projection of area as Proj.4 string
          | area_extent : list
          |     Area extent as a list (LL_x, LL_y, UR_x, UR_y)
        lonlats : list of lon lat pairs
            e.g. [(10,20),(20,30),...,(20,20)]
        outline : str or (R, G, B), optional
            line color
        width : float, optional
            line width
        outline_opacity : int, optional {0; 255}
            Opacity of lines
        x_offset : float, optional
            Pixel offset in x direction
        y_offset : float, optional
            Pixel offset in y direction
        """
        self._add_line(image, area_def, lonlats,
                       x_offset=x_offset, y_offset=y_offset,
                       fill=fill, fill_opacity=fill_opacity,
                       outline=outline, width=width,
                       outline_opacity=outline_opacity)

    def add_polygon(self, image, area_def, lonlats,
                    fill=None, fill_opacity=255, outline='white', width=1,
                    outline_opacity=255, x_offset=0, y_offset=0):
        """Add a user defined polygon from a list of (lon,lat) coordinates.

        :Parameters:
        image : object
            PIL image object
        area_def : list [proj4_string, area_extent]
          | proj4_string : str
          |     Projection of area as Proj.4 string
          | area_extent : list
          |     Area extent as a list (LL_x, LL_y, UR_x, UR_y)
        lonlats : list of lon lat pairs
            e.g. [(10,20),(20,30),...,(20,20)]
        fill : str or (R, G, B), optional
            Polygon fill color
        fill_opacity : int, optional {0; 255}
            Opacity of polygon fill
        outline : str or (R, G, B), optional
            line color
        width : float, optional
            line width
        outline_opacity : int, optional {0; 255}
            Opacity of lines
        x_offset : float, optional
            Pixel offset in x direction
        y_offset : float, optional
            Pixel offset in y direction
        """
        self._add_polygon(image, area_def, lonlats,
                          x_offset=x_offset, y_offset=y_offset,
                          fill=fill, fill_opacity=fill_opacity,
                          outline=outline, width=width,
                          outline_opacity=outline_opacity)

    def add_grid(self, image, area_def, (lon_major, lat_major),
                 (lon_minor, lat_minor),
                 font=None, write_text=True, fill=None, fill_opacity=255,
                 outline='white', width=1, outline_opacity=255,
                 minor_outline='white', minor_width=0.5,
                 minor_outline_opacity=255, minor_is_tick=True,
                 lon_placement='tb', lat_placement='lr'):
        """Add a lon-lat grid to a PIL image object

        :Parameters:
        image : object
            PIL image object
        proj4_string : str
            Projection of area as Proj.4 string
        (lon_major,lat_major): (float,float)
            Major grid line separation
        (lon_minor,lat_minor): (float,float)
            Minor grid line separation
        font: Aggdraw Font object, optional
            Font for major line markings
        write_text : boolean, optional
            Deterine if line markings are enabled
        fill_opacity : int, optional {0; 255}
            Opacity of text
        outline : str or (R, G, B), optional
            Major line color
        width : float, optional
            Major line width
        outline_opacity : int, optional {0; 255}
            Opacity of major lines
        minor_outline : str or (R, G, B), optional
            Minor line/tick color
        minor_width : float, optional
            Minor line width
        minor_outline_opacity : int, optional {0; 255}
            Opacity of minor lines/ticks
        minor_is_tick : boolean, optional
            Use tick minor line style (True) or full minor line style (False)
        """
        self._add_grid(image, area_def, lon_major, lat_major,
                       lon_minor, lat_minor,
                       font=font, write_text=write_text,
                       fill=fill, fill_opacity=fill_opacity, outline=outline,
                       width=width, outline_opacity=outline_opacity,
                       minor_outline=minor_outline, minor_width=minor_width,
                       minor_outline_opacity=minor_outline_opacity,
                       minor_is_tick=minor_is_tick,
                       lon_placement=lon_placement, lat_placement=lat_placement)

    def add_grid_to_file(self, filename, area_def, (lon_major, lat_major),
                         (lon_minor, lat_minor),
                         font=None, write_text=True,
                         fill=None, fill_opacity=255,
                         outline='white', width=1, outline_opacity=255,
                         minor_outline='white', minor_width=0.5,
                         minor_outline_opacity=255, minor_is_tick=True,
                         lon_placement='tb', lat_placement='lr'):
        """Add a lon-lat grid to an image

        :Parameters:
        image : object
            PIL image object
        proj4_string : str
            Projection of area as Proj.4 string
        (lon_major,lat_major): (float,float)
            Major grid line separation
        (lon_minor,lat_minor): (float,float)
            Minor grid line separation
        font: Aggdraw Font object, optional
            Font for major line markings
        write_text : boolean, optional
            Deterine if line markings are enabled
        fill_opacity : int, optional {0; 255}
            Opacity of text
        outline : str or (R, G, B), optional
            Major line color
        width : float, optional
            Major line width
        outline_opacity : int, optional {0; 255}
            Opacity of major lines
        minor_outline : str or (R, G, B), optional
            Minor line/tick color
        minor_width : float, optional
            Minor line width
        minor_outline_opacity : int, optional {0; 255}
            Opacity of minor lines/ticks
        minor_is_tick : boolean, optional
            Use tick minor line style (True) or full minor line style (False)
        """

        image = Image.open(filename)
        self.add_grid(image, area_def, (lon_major, lat_major),
                      (lon_minor, lat_minor),
                      font=font, write_text=write_text,
                      fill=fill, fill_opacity=fill_opacity,
                      outline=outline, width=width,
                      outline_opacity=outline_opacity,
                      minor_outline=minor_outline,
                      minor_width=minor_width,
                      minor_outline_opacity=minor_outline_opacity,
                      minor_is_tick=minor_is_tick,
                      lon_placement=lon_placement, lat_placement=lat_placement)
        image.save(filename)

    def add_coastlines(self, image, area_def, resolution='c', level=1,
                       fill=None, fill_opacity=255, outline='white', width=1,
                       outline_opacity=255, x_offset=0, y_offset=0):
        """Add coastlines to a PIL image object

        :Parameters:
        image : object
            PIL image object
        proj4_string : str
            Projection of area as Proj.4 string
        area_extent : list
            Area extent as a list (LL_x, LL_y, UR_x, UR_y)
        resolution : str, optional {'c', 'l', 'i', 'h', 'f'}
            Dataset resolution to use
        level : int, optional {1, 2, 3, 4}
            Detail level of dataset
        fill : str or (R, G, B), optional
            Land color
        fill_opacity : int, optional {0; 255}
            Opacity of land color
        outline : str or (R, G, B), optional
            Coastline color
        width : float, optional
            Width of coastline
        outline_opacity : int, optional {0; 255}
            Opacity of coastline color
        x_offset : float, optional
            Pixel offset in x direction
        y_offset : float, optional
            Pixel offset in y direction
        """

        self._add_feature(image, area_def, 'polygon', 'GSHHS',
                          resolution=resolution, level=level,
                          fill=fill, fill_opacity=fill_opacity,
                          outline=outline, width=width,
                          outline_opacity=outline_opacity, x_offset=x_offset,
                          y_offset=y_offset)

    def add_coastlines_to_file(self, filename, area_def, resolution='c',
                               level=1, fill=None, fill_opacity=255,
                               outline='white', width=1, outline_opacity=255,
                               x_offset=0, y_offset=0):
        """Add coastlines to an image file

        :Parameters:
        filename : str
            Image file
        proj4_string : str
            Projection of area as Proj.4 string
        area_extent : list
            Area extent as a list (LL_x, LL_y, UR_x, UR_y)
        resolution : str, optional {'c', 'l', 'i', 'h', 'f'}
            Dataset resolution to use
        level : int, optional {1, 2, 3, 4}
            Detail level of dataset
        fill : str or (R, G, B), optional
            Land color
        fill_opacity : int, optional {0; 255}
            Opacity of land color
        outline : str or (R, G, B), optional
            Coastline color
        width : float, optional
            Width of coastline
        outline_opacity : int, optional {0; 255}
            Opacity of coastline color
        x_offset : float, optional
            Pixel offset in x direction
        y_offset : float, optional
            Pixel offset in y direction
        """

        image = Image.open(filename)
        self.add_coastlines(image, area_def, resolution=resolution,
                            level=level, fill=fill,
                            fill_opacity=fill_opacity, outline=outline,
                            width=width, outline_opacity=outline_opacity,
                            x_offset=x_offset, y_offset=y_offset)
        image.save(filename)

    def add_borders(self, image, area_def, resolution='c', level=1,
                    outline='white', width=1, outline_opacity=255,
                    x_offset=0, y_offset=0):
        """Add borders to a PIL image object

        :Parameters:
        image : object
            PIL image object
        proj4_string : str
            Projection of area as Proj.4 string
        area_extent : list
            Area extent as a list (LL_x, LL_y, UR_x, UR_y)
        resolution : str, optional {'c', 'l', 'i', 'h', 'f'}
            Dataset resolution to use
        level : int, optional {1, 2, 3}
            Detail level of dataset
        outline : str or (R, G, B), optional
            Border color
        width : float, optional
            Width of coastline
        outline_opacity : int, optional {0; 255}
            Opacity of coastline color
        x_offset : float, optional
            Pixel offset in x direction
        y_offset : float, optional
            Pixel offset in y direction
        """

        self._add_feature(image, area_def, 'line', 'WDBII', tag='border',
                          resolution=resolution, level=level, outline=outline,
                          width=width, outline_opacity=outline_opacity,
                          x_offset=x_offset, y_offset=y_offset)

    def add_borders_to_file(self, filename, area_def, resolution='c',
                            level=1, outline='white', width=1,
                            outline_opacity=255, x_offset=0, y_offset=0):
        """Add borders to an image file

        :Parameters:
        image : object
            Image file
        proj4_string : str
            Projection of area as Proj.4 string
        area_extent : list
            Area extent as a list (LL_x, LL_y, UR_x, UR_y)
        resolution : str, optional {'c', 'l', 'i', 'h', 'f'}
            Dataset resolution to use
        level : int, optional {1, 2, 3}
            Detail level of dataset
        outline : str or (R, G, B), optional
            Border color
        width : float, optional
            Width of coastline
        outline_opacity : int, optional {0; 255}
            Opacity of coastline color
        x_offset : float, optional
            Pixel offset in x direction
        y_offset : float, optional
            Pixel offset in y direction
        """
        image = Image.open(filename)
        self.add_borders(image, area_def, resolution=resolution, level=level,
                         outline=outline, width=width,
                         outline_opacity=outline_opacity, x_offset=x_offset,
                         y_offset=y_offset)
        image.save(filename)

    def add_rivers(self, image, area_def, resolution='c', level=1,
                   outline='white', width=1, outline_opacity=255,
                   x_offset=0, y_offset=0):
        """Add rivers to a PIL image object

        :Parameters:
        image : object
            PIL image object
        proj4_string : str
            Projection of area as Proj.4 string
        area_extent : list
            Area extent as a list (LL_x, LL_y, UR_x, UR_y)
        resolution : str, optional {'c', 'l', 'i', 'h', 'f'}
            Dataset resolution to use
        level : int, optional {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11}
            Detail level of dataset
        outline : str or (R, G, B), optional
            River color
        width : float, optional
            Width of coastline
        outline_opacity : int, optional {0; 255}
            Opacity of coastline color
        x_offset : float, optional
            Pixel offset in x direction
        y_offset : float, optional
            Pixel offset in y direction
        """

        self._add_feature(image, area_def, 'line', 'WDBII', tag='river',
                          zero_pad=True, resolution=resolution, level=level,
                          outline=outline, width=width,
                          outline_opacity=outline_opacity, x_offset=x_offset,
                          y_offset=y_offset)

    def add_rivers_to_file(self, filename, area_def, resolution='c', level=1,
                           outline='white', width=1, outline_opacity=255,
                           x_offset=0, y_offset=0):
        """Add rivers to an image file

        :Parameters:
        image : object
            Image file
        proj4_string : str
            Projection of area as Proj.4 string
        area_extent : list
            Area extent as a list (LL_x, LL_y, UR_x, UR_y)
        resolution : str, optional {'c', 'l', 'i', 'h', 'f'}
            Dataset resolution to use
        level : int, optional {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11}
            Detail level of dataset
        outline : str or (R, G, B), optional
            River color
        width : float, optional
            Width of coastline
        outline_opacity : int, optional {0; 255}
            Opacity of coastline color
        x_offset : float, optional
            Pixel offset in x direction
        y_offset : float, optional
            Pixel offset in y direction
        """

        image = Image.open(filename)
        self.add_rivers(image, area_def, resolution=resolution, level=level,
                        outline=outline, width=width,
                        outline_opacity=outline_opacity, x_offset=x_offset,
                        y_offset=y_offset)
        image.save(filename)


def _get_lon_lat_bounding_box(area_extent, x_size, y_size, prj):
    """Get extreme lon and lat values
    """

    x_ll, y_ll, x_ur, y_ur = area_extent
    x_range = np.linspace(x_ll, x_ur, num=x_size)
    y_range = np.linspace(y_ll, y_ur, num=y_size)

    lons_s1, lats_s1 = prj(np.ones(y_range.size) * x_ll, y_range, inverse=True)
    lons_s2, lats_s2 = prj(x_range, np.ones(x_range.size) * y_ur, inverse=True)
    lons_s3, lats_s3 = prj(np.ones(y_range.size) * x_ur, y_range, inverse=True)
    lons_s4, lats_s4 = prj(x_range, np.ones(x_range.size) * y_ll, inverse=True)

    angle_sum = 0
    prev = None
    for lon in np.concatenate((lons_s1, lons_s2,
                               lons_s3[::-1], lons_s4[::-1])):
        if prev is not None:
            delta = lon - prev
            if abs(delta) > 180:
                delta = (abs(delta) - 360) * np.sign(delta)
            angle_sum += delta
        prev = lon

    if round(angle_sum) == -360:
        # Covers NP
        lat_min = min(lats_s1.min(), lats_s2.min(),
                      lats_s3.min(), lats_s4.min())
        lat_max = 90
        lon_min = -180
        lon_max = 180
    elif round(angle_sum) == 360:
        # Covers SP
        lat_min = -90
        lat_max = max(lats_s1.max(), lats_s2.max(),
                      lats_s3.max(), lats_s4.max())
        lon_min = -180
        lon_max = 180
    elif round(angle_sum) == 0:
        # Covers no poles
        if np.sign(lons_s1[0]) * np.sign(lons_s1[-1]) == -1:
            # End points of left side on different side of dateline
            lon_min = lons_s1[lons_s1 > 0].min()
        else:
            lon_min = lons_s1.min()

        if np.sign(lons_s3[0]) * np.sign(lons_s3[-1]) == -1:
            # End points of right side on different side of dateline
            lon_max = lons_s3[lons_s3 < 0].max()
        else:
            lon_max = lons_s3.max()

        lat_min = lats_s4.min()
        lat_max = lats_s2.max()
    else:
        # Pretty strange
        lat_min = -90
        lat_max = 90
        lon_min = -180
        lon_max = 180

    if not (-180 <= lon_min <= 180):
        lon_min = -180
    if not (-180 <= lon_max <= 180):
        lon_max = 180
    if not (-90 <= lat_min <= 90):
        lat_min = -90
    if not (-90 <= lat_max <= 90):
        lat_max = 90

    return lon_min, lon_max, lat_min, lat_max


def _get_pixel_index(shape, area_extent, x_size, y_size, prj,
                     x_offset=0, y_offset=0):
    """Map coordinates of shape to image coordinates
    """

    # Get shape data as array and reproject
    shape_data = np.array(shape.points)
    lons = shape_data[:, 0]
    lats = shape_data[:, 1]

    x_ll, y_ll, x_ur, y_ur = area_extent

    x_loc, y_loc = prj(lons, lats)

    # Handle out of bounds
    i = 0
    segments = []
    if 1e30 in x_loc or 1e30 in y_loc:
        # Split polygon in line segments within projection
        is_reduced = True
        if x_loc[0] == 1e30 or y_loc[0] == 1e30:
            in_segment = False
        else:
            in_segment = True

        for j in range(x.size):
            if (x_loc[j] == 1e30 or y_loc[j] == 1e30):
                if in_segment:
                    segments.append((x_loc[i:j], y_loc[i:j]))
                    in_segment = False
            elif not in_segment:
                in_segment = True
                i = j
        if in_segment:
            segments.append((x_loc[i:], y_loc[i:]))

    else:
        is_reduced = False
        segments = [(x_loc, y_loc)]

    # Convert to pixel index coordinates
    l_x = (x_ur - x_ll) / x_size
    l_y = (y_ur - y_ll) / y_size

    index_arrays = []
    for x_loc, y_loc in segments:
        n_x = ((-x_ll + x_loc) / l_x) + 0.5 + x_offset
        n_y = ((y_ur - y_loc) / l_y) + 0.5 + y_offset

        index_array = np.vstack((n_x, n_y)).T
        index_arrays.append(index_array)

    return index_arrays, is_reduced
