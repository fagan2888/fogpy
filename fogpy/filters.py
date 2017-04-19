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

"""This module implements an basic algorithm filter class
and several class instances for satellite fog detection applications"""

import copy_reg
import logging
import matplotlib.pyplot as plt
import numpy as np
import os
import types

from collections import defaultdict
from datetime import datetime
from matplotlib.cm import get_cmap
import multiprocessing as mp
from pyorbital import astronomy
from scipy.signal import find_peaks_cwt
from scipy import ndimage

from lowwatercloud import LowWaterCloud

logger = logging.getLogger(__name__)


# Add new pickle method, required for multiprocessing to run class instance
# methods in parallel
def _pickle_method(m):
    if m.im_self is None:
        return getattr, (m.im_class, m.im_func.func_name)
    else:
        return getattr, (m.im_self, m.im_func.func_name)

copy_reg.pickle(types.MethodType, _pickle_method)


class NotApplicableError(Exception):
    """Exception to be raised when a filter is not applicable."""
    pass


class BaseArrayFilter(object):
    """This super filter class provide all functionalities to apply a filter
    funciton on a given numpy array representing a satellite image and return
    the filtered masked array as result"""
    def __init__(self, arr, **kwargs):
        if isinstance(arr, np.ma.MaskedArray):
            self.arr = arr
            self.inmask = arr.mask
        elif isinstance(arr, np.ndarray):
            self.arr = arr
            self.inmask = None
        else:
            raise ImportError('The filter <{}> needs a valid 2d numpy array '
                              'as input'.format(self.__class__.__name__))
        if kwargs is not None:
            for key, value in kwargs.iteritems():
                self.__setattr__(key, value)
            self.result = None
            self.mask = None
        # Get class name
        self.name = self.__str__().split(' ')[0].split('.')[-1]
        # Set time
        if not hasattr(self, 'time'):
            self.time = datetime.now()
            logger.debug('Setting filter reference time to current time: {}'
                         .format(self.time))
        # Set plotting attribute
        if not hasattr(self, 'save'):
            self.save = False
        if not hasattr(self, 'plot'):
            self.plot = False
        if not hasattr(self, 'dir'):
            self.dir = '/tmp'
        if not hasattr(self, 'bg_img'):
            self.bg_img = self.arr
        if not hasattr(self, 'resize'):
            self.resize = 0
        # Get number of cores
        if not hasattr(self, 'nprocs'):
            self.nprocs = mp.cpu_count()

    def apply(self):
        """Apply the given filter function"""
        if self.isapplicable():
            self.filter_function()
            self.check_results()
        else:
            raise NotApplicableError('Array filter <{}> is not applicable'
                                     .format(self.__class__.__name__))

        return self.result, self.mask

    def isapplicable(self):
        """Test filter applicability"""
        ret = []
        for attr in self.attrlist:
            if hasattr(self, attr):
                ret.append(True)
            else:
                ret.append(False)
                logger.warning("Missing input attribute: {}".format(attr))

        return all(ret)

    def filter_function(self):
        """Filter routine"""
        self.mask = np.ones(self.arr.shape) == 1

        self.result = np.ma.array(self.arr, mask=self.mask)

        return True

    def check_results(self):
        """Check filter results for plausible results"""
        self.filter_stats()
        if self.plot:
            self.plot_filter(self.save, self.dir, self.resize)
        ret = True
        return ret

    def filter_stats(self):
        self.filter_size = self.mask.size
        self.filter_num = np.nansum(self.mask)
        if self.inmask is None:
            self.inmask_num = 0
            self.new_masked = self.filter_num
            self.remain_num = self.filter_size - self.filter_num
        else:
            self.inmask_num = np.nansum(self.inmask)
            self.new_masked = np.nansum(~self.inmask & self.mask)
            self.remain_num = np.nansum(~self.mask & ~self.inmask)

        logger.info("""---- Filter results for {} ---- \n
                    {}
                    Array size:              {}
                    Masking:                 {}
                    Previous masked:         {}
                    New filtered:            {}
                    Remaining:               {}"""
                    .format(self.name, self.__doc__,
                            self.filter_size, self.filter_num, self.inmask_num,
                            self.new_masked, self.remain_num))

    def plot_filter(self, save=False, dir="/tmp", resize=0):
        """Plotting the filter result"""
        # Get output directory and image name
        savedir = os.path.join(dir, self.name + '_' +
                               datetime.strftime(self.time,
                                                 '%Y%m%d%H%M') + '.png')
        maskdir = os.path.join(dir, self.name + '_mask_' +
                               datetime.strftime(self.time,
                                                 '%Y%m%d%H%M') + '.png')
        # Using Trollimage if available, else matplotlib is used to plot
        try:
            from trollimage.image import Image
            from trollimage.colormap import Colormap
        except:
            cmap = get_cmap('gray')
            cmap.set_bad('goldenrod', 1.)
            imgplot = plt.imshow(self.result.squeeze(), cmap=cmap)
            plt.axis('off')
            if save:
                plt.savefig(savedir)
                logger.info("{} results are plotted to: {}". format(self.name,
                                                                    self.dir))
            else:
                plt.show()
        # Define custom fog colormap
        fogcol = Colormap((0., (250 / 255.0, 200 / 255.0, 40 / 255.0)),
                          (1., (1.0, 1.0, 229 / 255.0)))
        maskcol = Colormap((1., (230 / 255.0, 50 / 255.0, 50 / 255.0)))
        # Create image from data
        if self.result is None:
            self.result = self.arr
        filter_img = Image(self.result.squeeze(), mode='L', fill_value=None)
        filter_img.stretch("crude")
        filter_img.invert()
        filter_img.colorize(fogcol)
        # Get background image
        bg_img = Image(self.bg_img.squeeze(), mode='L', fill_value=None)
        bg_img.stretch("crude")
        bg_img.convert("RGB")
        bg_img.invert()
        if resize != 0:
            if not isinstance(resize, int):
                resize = int(resize)
            bg_img.resize((self.bg_img.shape[0] * resize,
                           self.bg_img.shape[1] * resize))
            filter_img.resize((self.result.shape[0] * resize,
                               self.result.shape[1] * resize))

        try:
            # Merging
            filter_img.merge(bg_img)
        except:
            logger.warning("No merging for filter plot possible")
        if save:
            filter_img.save(savedir)
            logger.info("{} results are plotted to: {}". format(self.name,
                                                                self.dir))
        else:
            filter_img.show()
        # Create mask image
        if isinstance(self.result, np.ma.masked_array):
            mask = self.result.mask.squeeze()
            mask = np.ma.masked_where(mask == 1, mask)
            mask_img = Image(mask, mode='L', fill_value=None)
            mask_img.stretch("crude")
            mask_img.invert()
            mask_img.colorize(fogcol)
            if resize != 0:
                if not isinstance(resize, int):
                    resize = int(resize)
                mask_img.resize((self.result.shape[0] * resize,
                                 self.result.shape[1] * resize))
            # mask_img.merge(bg_img)
            if save:
                mask_img.save(maskdir)
            else:
                mask_img.show()


class CloudFilter(BaseArrayFilter):
    """Cloud filtering for satellite images.
    """
    # Required inputs
    attrlist = ['ir108', 'ir039']

    def filter_function(self):
        """Cloud filter routine

        Given the combination of a solar and a thermal signal at 3.9 μm,
        the difference in radiances to the 10.8 μm must be larger for a
        cloud-contaminated pixel than for a clear pixel.
        In the histogram of the difference the clear sky peak is identified
        within a certain range. The nearest significant relative minimum in the
        histogram towards more negative values is detected and used as a
        threshold to separate clear from cloudy pixels in the image.
        """
        logger.info("Applying Cloud Filter")

        # Infrared channel difference
        self.cm_diff = np.ma.asarray(self.ir108 - self.ir039)

        # Create histogram
        self.hist = (np.histogram(self.cm_diff.compressed(), bins='auto'))

        # Find local min and max values
        peaks = np.sign(np.diff(self.hist[0]))
        localmin = (np.diff(peaks) > 0).nonzero()[0] + 1
        localmax = (np.diff(peaks) < 0).nonzero()[0] + 1

        # Utilize scipy signal funciton to find peaks
        peakind = find_peaks_cwt(self.hist[0],
                                 np.arange(1, len(self.hist[1]) / 10))
        peakrange = self.hist[1][peakind][(self.hist[1][peakind] >= -10) &
                                          (self.hist[1][peakind] < 10)]
        self.minpeak = np.min(peakrange)
        self.maxpeak = np.max(peakrange)

        # Determine threshold
        logger.debug("Histogram range for cloudy/clear sky pixels: {} - {}"
                     .format(self.minpeak, self.maxpeak))
        thres_index = localmin[(self.hist[1][localmin] <= self.maxpeak) &
                               (self.hist[1][localmin] >= self.minpeak) &
                               (self.hist[1][localmin] < 0.5)]
        self.thres = np.max(self.hist[1][thres_index])

        if self.thres > 0 or self.thres < -5:
            logger.warning("Cloud maks difference threshold {} outside normal"
                           " range (from -5 to 0)".format(self.thres))
        else:
            logger.debug("Cloud mask difference threshold set to %s"
                         .format(self.thres))
        # Create cloud mask for image array
        self.mask = self.cm_diff > self.thres

        self.result = np.ma.array(self.arr, mask=self.mask)

        return True

    def plot_cloud_hist(self, saveto=None):
        plt.bar(self.hist[1][:-1], self.hist[0])
        plt.title("Histogram with 'auto' bins")
        if saveto is None:
            plt.show()
        else:
            plt.savefig(saveto)


class SnowFilter(BaseArrayFilter):
    """Snow filtering for satellite images.
    """
    # Required inputs
    attrlist = ['vis006', 'vis008', 'nir016', 'ir108']

    def filter_function(self):
        """Snow filter routine

        Snow has a certain minimum reflectance (0.11 at 0.8 μm) and snow has a
        certain minimum temperature (256 K)
        Snow displays a lower reflectivity than water clouds at 1.6 μm,
        combined with a slightly higher level of absorption
        (Wiscombe and Warren, 1980)
        thresholds are applied in combination with the Normalized Difference
        Snow Index
        """
        logger.info("Applying Snow Filter")
        # Calculate Normalized Difference Snow Index
        self.ndsi = (self.vis006 - self.nir016) / (self.vis006 + self.nir016)

        # Where the NDSI exceeds a certain threshold (0.4) and the two other
        # criteria are met, a pixel is rejected as snow-covered.
        # Create snow mask for image array
        temp_thres = (self.vis008 / 100 >= 0.11) & (self.ir108 >= 256)
        ndsi_thres = self.ndsi >= 0.4
        # Create snow mask for image array
        self.mask = temp_thres & ndsi_thres

        self.result = np.ma.array(self.arr, mask=self.mask)

        return True


class IceCloudFilter(BaseArrayFilter):
    """Ice cloud filtering for satellite images.
    """
    # Required inputs
    attrlist = ['ir120', 'ir087', 'ir108']

    def filter_function(self):
        """Ice cloud filter routine

        Difference of brightness temperatures in the 12.0 and 8.7 μm channels
        is used as an indicator of cloud phase (Strabala et al., 1994).
        Where it exceeds 2.5 K, a water-cloud-covered pixel is assumed with a
        large degree of certainty. This is combined with a straightforward
        temperature test, cutting off at very low 10.8 μm brightness
        temperatures (250 K).
        """
        logger.info("Applying Snow Filter")
        # Apply infrared channel difference
        self.ic_diff = self.ir120 - self.ir087
        # Create ice cloud mask
        ice_mask = (self.ic_diff < 2.5) | (self.ir108 < 250)
        # Create snow mask for image array
        self.mask = ice_mask

        self.result = np.ma.array(self.arr, mask=self.mask)

        return True


class CirrusCloudFilter(BaseArrayFilter):
    """Thin cirrus cloud filtering for satellite images.
    """
    # Required inputs
    attrlist = ['ir120', 'ir087', 'ir108', 'lat', 'lon', 'time']

    def filter_function(self):
        """Ice cloud filter routine

        Thin cirrus is detected by means of the split-window IR channel
        brightness temperature difference (T10.8 –T12.0 ). This difference is
        compared to a threshold dynamically interpolated from a lookup table
        based on satellite zenith angle and brightness temperature at 10.8 μm
        (Saunders and Kriebel, 1988)
        In addtion a second strong cirrus test (T8.7–T10.8), founded on the
        relatively strong cirrus signal at the former wavelength is applied
        (Wiegner et al.1998). Where the difference is greater than 0 K, cirrus
        is assumed to be present.
        """
        logger.info("Applying Cirrus Filter")
        # Get infrared channel difference
        self.bt_diff = self.ir108 - self.ir120
        # Calculate sun zenith angles
        sza = astronomy.sun_zenith_angle(self.time, self.lon, self.lat)
        minsza = np.min(sza)
        maxsza = np.max(sza)
        logger.debug("Found solar zenith angles from %s to %s°" % (minsza,
                                                                   maxsza))
        # Calculate secant of sza
        secsza = 1 / np.cos(np.deg2rad(sza))

        # Apply lut to BT and sza values
        # Vectorize LUT functions for numpy arrays
        vfind_nearest_lut_sza = np.vectorize(self.find_nearest_lut_sza)
        vfind_nearest_lut_bt = np.vectorize(self.find_nearest_lut_bt)
        vapply_lut = np.vectorize(self.apply_lut)

        secsza_lut = vfind_nearest_lut_sza(secsza)
        chn108_ma_lut = vfind_nearest_lut_bt(self.ir108)

        self.bt_thres = vapply_lut(secsza_lut, chn108_ma_lut)
        logger.debug("Set BT difference thresholds for cirrus: {} to {} K"
                     .format(np.min(self.bt_thres), np.max(self.bt_thres)))
        # Create thin cirrus mask
        self.bt_ci_mask = self.bt_diff > self.bt_thres

        # Strong cirrus test
        self.strong_ci_diff = self.ir087 - self.ir108
        self.strong_ci_mask = self.strong_ci_diff > 0
        cirrus_mask = self.bt_ci_mask | self.strong_ci_mask

        # Create snow mask for image array
        self.mask = cirrus_mask

        self.result = np.ma.array(self.arr, mask=self.mask)

        return True

    def find_nearest_lut_sza(self, sza):
        """ Get nearest look up table key value for given ssec(sza)"""
        sza_opt = [1.0, 1.25, 1.50, 1.75, 2.0]
        sza_idx = np.array([np.abs(sza - i) for i in sza_opt]).argmin()
        return(sza_opt[sza_idx])

    def find_nearest_lut_bt(self, bt):
        """ Get nearest look up table key value for given BT"""
        bt_opt = [260, 270, 280, 290, 300, 310]
        bt_idx = np.array([np.abs(bt - i) for i in bt_opt]).argmin()
        return(bt_opt[bt_idx])

    def apply_lut(self, sza, bt):
        """ Apply LUT to given BT and sza values"""
        return(self.lut[bt][sza])
    # Lookup table for BT difference thresholds at certain sec(sun zenith
    # angles) and 10.8 μm BT
    lut = {260: {1.0: 0.55, 1.25: 0.60, 1.50: 0.65, 1.75: 0.90, 2.0: 1.10},
           270: {1.0: 0.58, 1.25: 0.63, 1.50: 0.81, 1.75: 1.03, 2.0: 1.13},
           280: {1.0: 1.30, 1.25: 1.61, 1.50: 1.88, 1.75: 2.14, 2.0: 2.30},
           290: {1.0: 3.06, 1.25: 3.72, 1.50: 3.95, 1.75: 4.27, 2.0: 4.73},
           300: {1.0: 5.77, 1.25: 6.92, 1.50: 7.00, 1.75: 7.42, 2.0: 8.43},
           310: {1.0: 9.41, 1.25: 11.22, 1.50: 11.03, 1.75: 11.60, 2.0: 13.39}}


class WaterCloudFilter(BaseArrayFilter):
    """Water cloud filtering for satellite images.
    """
    # Required inputs
    attrlist = ['vis006', 'nir016', 'ir039', 'cloudmask']

    def filter_function(self):
        """Water cloud filter routine

        Apply a weaker cloud phase test in order to get an estimate regarding
        their phase. This test uses the NDSI introduced above. Where it falls
        below 0.1, a water cloud is assumed to be present.
        Afterwards a small droplet proxy tes is being performed. Fog generally
        has a stronger signal at 3.9 μm than clear ground, which in turn
        radiates more than other clouds. The 3.9 μm radiances for cloud-free
        land areas are averaged over 50 rows at a time to obtain an
        approximately latitudinal value. Wherever a cloud-covered pixel
        exceeds this value, it is flagged.
        """
        logger.info("Applying Water Cloud Filter")
        # Weak water cloud test with NDSI
        self.ndsi_ci = (self.vis006 - self.nir016) / (self.vis006 +
                                                      self.nir016)
        water_mask = self.ndsi_ci > 0.1
        # Small droplet proxy test
        # Get only cloud free pixels
        cloud_free_ma = np.ma.masked_where(~self.cloudmask, self.ir039)
        # Latitudinal average cloud free radiances
        self.lat_cloudfree = np.ma.mean(cloud_free_ma, 1)
        logger.debug("Mean latitudinal threshold for cloudfree areas: %.2f K"
                     % np.mean(self.lat_cloudfree))
        self.line = 0
        # Apply latitudinal threshold to cloudy areas
        drop_mask = np.apply_along_axis(self.find_watercloud, 1, self.ir039,
                                        self.lat_cloudfree)

        # Create snow mask for image array
        self.mask = water_mask | drop_mask

        self.result = np.ma.array(self.arr, mask=self.mask)

        return True

    def find_watercloud(self, lat, thres):
                """Funciton to compare row of BT with given latitudinal thresholds
                """
                if not isinstance(lat, np.ma.masked_array):
                    lat = np.ma.MaskedArray(lat, mask=np.zeros(lat.shape))
                if all(lat.mask):
                    res = lat.mask
                elif np.ma.is_masked(thres[self.line]):
                    res = lat <= np.mean(self.lat_cloudfree)
                else:
                    res = lat <= thres[self.line]
                self.line += 1

                return(res)


class SpatialCloudTopHeightFilter(BaseArrayFilter):
    """Filtering cloud clusters by height for satellite images.
    """
    # Required inputs
    attrlist = ['ir108', 'clusters', 'cluster_z']

    def filter_function(self):
        """Cloud cluster filter routine

        This filter utilizes spatially clustered cloud objects and their
        cloud top height to mask cloud clusters with cloud top height above
        2000 m.
        """
        logger.info("Applying Spatial Clustering Cloud Top Height Filter")
        # Apply maximum threshold for cluster height to identify low fog clouds
        cluster_mask = self.clusters.mask
        for key, item in self.cluster_z.iteritems():
            if any([c > 2000 for c in item]):
                cluster_mask[self.clusters == key] = True

        # Create additional fog cluster map
        self.cluster_cth = np.ma.masked_where(cluster_mask, self.clusters)
        for key, item in self.cluster_z.iteritems():
            if all([c <= 2000 for c in item]):
                self.cluster_cth[self.cluster_cth == key] = np.mean(item)

        # Create cluster mask for image array
        self.mask = cluster_mask

        self.result = np.ma.array(self.arr, mask=self.mask)

        return True


class SpatialHomogeneityFilter(BaseArrayFilter):
    """Filtering cloud clusters by StDev for satellite images.
    """
    # Required inputs
    attrlist = ['ir108', 'clusters']

    def filter_function(self):
        """Cloud cluster filter routine

        This filter utilizes spatially clustered cloud objects and their
        cloud top height to mask cloud clusters with  with spatial inhomogen
        cloud clusters. Cloud top height standard deviation less than 2.5 are
        filtered.
        """
        logger.info("Applying Spatial Clustering Inhomogeneity Filter")
        # Surface homogeneity test
        cluster_mask = self.inmask
        cluster, nlbl = ndimage.label(~self.clusters.mask)
        cluster_ma = np.ma.masked_where(self.inmask, self.clusters)

        cluster_sd = ndimage.standard_deviation(self.ir108, cluster_ma,
                                                index=np.arange(1, nlbl+1))

        # 4. Mask potential fog clouds with high spatial inhomogeneity
        sd_mask = cluster_sd > 2.5
        cluster_dict = {key: sd_mask[key - 1] for key in np.arange(1, nlbl+1)}
        for val in np.arange(1, nlbl+1):
            cluster_mask[cluster_ma == val] = cluster_dict[val]

        # Create cluster mask for image array
        self.mask = cluster_mask

        self.result = np.ma.array(self.arr, mask=self.mask)

        return True


class CloudPhysicsFilter(BaseArrayFilter):
    """Filtering cloud microphysics for satellite images.
    """
    # Required inputs
    attrlist = ['reff', 'cot']

    def filter_function(self):
        """Cloud microphysics filter routine

        Typical microphysical parameters for fog were taken from studies.
        Fog optical depth normally ranges between 0.15 and 30 while droplet
        effective radius varies between 3 and 12 μm, with a maximum of 20 μm in
        coastal fog. The respective maxima for optical depth (30) and droplet
        radius (20 μm) are applied to the low stratus mask as cut-off levels.
        Where a pixel previously identified as fog/low stratus falls outside
        the range it will now be flagged as a non-fog pixel.
        """
        logger.info("Applying Spatial Clustering Inhomogenity Filter")

        if np.ma.isMaskedArray(self.cot):
                self.cot = self.cot.base
        if np.ma.isMaskedArray(self.reff):
            self.reff = self.reff.base
        # Add mask by microphysical thresholds
        cpp_mask = (self.cot > 30) | (self.reff > 20e-6)
        # Create cloud physics mask for image array
        self.mask = cpp_mask

        self.result = np.ma.array(self.arr, mask=self.mask)

        return True


class LowCloudFilter(BaseArrayFilter):
    """Filtering low clouds for satellite images.
    """
    # Required inputs
    attrlist = ['lwp', 'cth', 'ir108', 'clusters', 'reff', 'elev']

    # Correction factor for 3.7 um LWP retrievals
    lwp_corr = 0.88  # Reference: (Platnick 2000)

    def filter_function(self):
        """Cloud microphysics filter routine

        The filter separate low stratus clouds from ground fog clouds
        by computing the cloud base height with a 1D low cloud model for
        each cloud cluster.
        """
        logger.info("Applying Low Cloud Filter")
        # Creating process pool
        pool = mp.Pool(self.nprocs)
        mlogger = mp.log_to_stderr()
        mlogger.setLevel(logging.DEBUG)
        # Declare result arrays without copy
        self.cbh = np.empty(self.clusters.shape, dtype=np.float)
        self.fbh = np.empty(self.clusters.shape, dtype=np.float)
        self.fog_mask = self.clusters.mask
        # Compute mean values for cloud clusters
        lwp_cluster = self.get_cluster_mean(self.clusters, self.lwp * 1000,
                                            exclude=[0])
        cth_cluster = self.get_cluster_mean(self.clusters, self.cth,
                                            exclude=[0])
        ctt_cluster = self.get_cluster_mean(self.clusters, self.ir108)
        reff_cluster = self.get_cluster_mean(self.clusters, self.reff, [],
                                             False)
        # Loop over processes
        logger.info("Run low cloud models")
        # Get pool result list
        self.result_list = []
        for key in lwp_cluster.keys():
            workinput = [lwp_cluster[key], cth_cluster[key], ctt_cluster[key],
                         reff_cluster[key]]
            pool.apply_async(self.get_fog_base_height, args=workinput,
                             callback=self.log_result)
        # Wait for all processes to finish
        pool.close()
        pool.join()
        # Create ground fog and low stratus cloud masks and cbh
        keys = lwp_cluster.keys()
        for i, res in enumerate(self.result_list):
            self.cbh[self.clusters == keys[i]] = res[0]
            self.fbh[self.clusters == keys[i]] = res[1]
            # Mask non ground fog clouds
            self.fog_mask[(self.clusters == keys[i]) & (self.fbh -
                                                        self.elev.squeeze() >
                                                        0)] = True
        # Create cloud physics mask for image array
        self.mask = self.fog_mask

        self.result = np.ma.array(self.arr, mask=self.mask)

        return True

    def log_result(self, result):
        # This is called whenever a pool(i) returns a result.
        # Results are modified only by the main process, not the pool workers.
        self.result_list.append(result)

    def get_fog_base_height(self, cwp, cth, ctt, reff):
        """ Calculate fog base heights for low cloud pixels with a
        numerical 1-D low cloud model and known liquid water path, cloud top
        height / temperature and droplet effective radius from satellite
        retrievals
        """
        lowcloud = LowWaterCloud(cth=cth,
                                 ctt=ctt,
                                 cwp=cwp * self.lwp_corr,
                                 cbh=0,
                                 reff=reff)
        try:
            # Calculate cloud base height
            cbh = lowcloud.get_cloud_base_height(-100, 'basin')
            # Get visibility and fog cloud base height
            fbh = lowcloud.get_fog_base_height()
        except:
            cbh = np.nan
            fbh = np.nan

        return cbh, fbh

    def get_cluster_mean(self, clusters, values, exclude=[0], noneg=True):
        """Calculate the mean of an array of values for given cluster
        structures
        """
        result = defaultdict(list)
        if np.ma.isMaskedArray(clusters):
            clusters = clusters.filled(0)
        # Calculate mean values for clusters
        for index, key in np.ndenumerate(clusters):
            if key != 0:

                val = values[index]
                if val in exclude:
                    # Remove exluced values
                    val = np.nan
                elif val < 0 and noneg:
                    # Optional remove of negative values
                    val = np.nan
                # Add value to result dict
                result[key].append(val)
        # Calculate average cluster values by dictionary key
        result = {k: np.nanmean(v) for k, v in result.iteritems()}

        return result
