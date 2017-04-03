#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (c) 2017
# Author(s):
#   Thomas Leppelt <thomas.leppelt@dwd.de>

# This file is part of the fogpy package.

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""This module implements an base satellite algorithm class"""

import logging
import matplotlib.pyplot as plt
import numpy as np

from matplotlib.cm import get_cmap
from filters import CloudFilter
from filters import SnowFilter
from filters import IceCloudFilter
from filters import CirrusCloudFilter

logger = logging.getLogger(__name__)


class NotProcessibleError(Exception):
    """Exception to be raised when a filter is not applicable."""
    pass


class BaseSatelliteAlgorithm(object):
    """This super filter class provide all functionalities to run an algorithm
    on satellite image arrays and return a new array as result"""
    def __init__(self, **kwargs):
        self.inmask = None
        self.mask = None
        self.result = None
        self.attributes = []
        if kwargs is not None:
            for key, value in kwargs.iteritems():
                self.attributes.append(key)
                if isinstance(value, np.ma.MaskedArray):
                    self.shape = value.shape
                    self.set_mask(value.mask)
                elif isinstance(value, np.ndarray):
                    self.shape = value.shape

                self.__setattr__(key, value)

    def run(self):
        """Start the algorithm and return results"""
        if self.isprocessible():
            self.procedure()
            self.check_results()
        else:
            raise NotProcessibleError('Satellite algorithm <{}> is not '
                                      'processible'
                                      .format(self.__class__.__name__))

        return(self.result, self.mask)

    def isprocessible(self):
        """Test runability here"""
        ret = True

        return(ret)

    def procedure(self):
        """Define algorithm procedure here"""
        self.mask = np.ones(self.shape) == 1

        self.result = np.ma.array(np.ones(self.shape), mask=self.mask)

        return(True)

    def check_results(self):
        """Check processed algorithm for plausible results"""
        ret = True
        return(ret)

    def set_mask(self, mask):
        """Compute the new array mask as union of all input array masks
        and computed masks"""
        if self.inmask is not None:
            self.inmask = self.inmask | mask
        else:
            self.inmask = mask

    def get_kwargs(self, keys):
        """Return dictionary with passed keyword arguments"""
        return({key: self.__getattribute__(key) for key in self.attributes
                if key in keys})

    def plot_result(self):
        """Plotting the filter result"""
        cmap = get_cmap('gray')
        cmap.set_bad('goldenrod', 1.)
        imgplot = plt.imshow(self.result.squeeze(), cmap=cmap)
        plt.show()


class FogLowStratusAlgorithm(BaseSatelliteAlgorithm):
    """This algorithm implements a fog and low stratus detection and forecasting
     for geostationary satellite images from the SEVIRI instrument onboard of
     METEOSAT second generation MSG satellites.
     It is utilizing the methods proposed in different innovative studies:

         - A novel approach to fog/low stratus detection using Meteosat 8 data
            J. Cermak & J. Bendix
        - Detecting ground fog from space – a microphysics-based approach
            J. Cermak & J. Bendix

    Arguements:
        chn108    Array for the 10.8 μm channel
        chn39    Array for the 3.9 μm channel
        chn08    Array for the 0.8 μm channel
        chn16    Array for the 1.6 μm channel
        chn06    Array for the 0.6 μm channel
        chn87    Array for the 8.7 μm channel
        chn120    Array for the 12.0 μm channel
        time    Datetime object for the satellite scence
        lat    Array of latitude values
        lon    Array of longitude values
        elevation Array of area elevation
        cot    Array of cloud optical thickness (depth)
        reff    Array of cloud particle effective raduis

    Returns:
        Infrared image with fog mask

    - A novel approach to fog/low stratus detection using Meteosat 8 data
            J. Cermak & J. Bendix
    - Detecting ground fog from space – a microphysics-based approach
            J. Cermak & J. Bendix

    The algorithm can be applied to satellite zenith angle lower than 70°
    and a maximum solar zenith angle of 80°.

    The algorithm workflow is a succession of differnt masking approaches
    from coarse to finer selection to find fog and low stratus clouds within
    provided satellite images. Afterwards a separation between fog and low
    clouds are made by calibrating a cloud base height with a low cloud model
    to satellite retrieval information. Then a fog dissipation and subsequently
    a nowcasting of fog can be done.

            Input: Calibrated satellite images >-----   Implemented:
                                                    |
                1.  Cloud masking -------------------    yes
                                                    |
                2.  Snow masking --------------------    yes
                                                    |
                3.  Ice cloud masking ---------------    yes
                                                    |
                4.  Thin cirrus masking -------------    ---
                                                    |
                5.  Droplet radius test -------------    ---
                                                    |
                6.  Spatial clustering---------------
                                                    |
                7.  Maximum margin elevation --------
                                                    |
                8.  Surface homogenity check --------
                                                    |
                9.  Microphysics plausibility check -
                                                    |
                10.  Differenciate fog - low status -
                                                    |
                11.  Fog dissipation ----------------
                                                    |
                12.  Nowcasting ---------------------
                                                    |
            Output: fog and low stratus mask <-------
     """
    def isprocessible(self):
        """Test runability here"""
        attrlist = ['ir108', 'ir039', 'vis008', 'nir016', 'vis006', 'ir087',
                    'ir120', 'lat', 'lon', 'time']
        ret = []
        for attr in attrlist:
            if hasattr(self, attr):
                ret.append(True)
            else:
                ret.append(False)
                logger.warning("Missing input attribute: {}".format(attr))

        return(all(ret))

    def procedure(self):
        """ Apply different filters and low cloud model to input data"""
        logger.info("Starting fog and low cloud detection algorithm")
        # 1. Cloud filtering
        cloud_input = self.get_kwargs(['ir108', 'ir039'])
        cloudfilter = CloudFilter(cloud_input['ir108'], **cloud_input)
        cloudfilter.apply()

        # 2. Snow filtering
        snow_input = self.get_kwargs(['ir108', 'vis008', 'nir016', 'vis006'])
        snowfilter = SnowFilter(cloudfilter.result, **snow_input)
        snowfilter.apply()

        # 3. Ice cloud detection
        # Ice cloud exclusion - Only warm fog (i.e. clouds in the water phase)
        # are considered. Warning: No ice fog detection wiht this filter option
        ice_input = self.get_kwargs(['ir120', 'ir087', 'ir108'])
        icefilter = IceCloudFilter(snowfilter.result, **ice_input)
        icefilter.apply()

        # 4. Cirrus cloud filtering
        cirrus_input = self.get_kwargs(['ir120', 'ir087', 'ir108', 'lat',
                                        'lon', 'time'])
        cirrusfilter = CirrusCloudFilter(icefilter.result, **cirrus_input)
        cirrusfilter.apply()

        # Set results
        self.result = cirrusfilter.result
        self.mask = cirrusfilter.mask

        return(True)

    def check_results(self):
        """Check processed algorithm for plausible results"""
        ret = True
        return(ret)