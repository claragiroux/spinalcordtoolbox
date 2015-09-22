#!/usr/bin/env python
#
# This program returns the gray matter segmentation given anatomical, spinal cord segmentation and t2star images
#
# ---------------------------------------------------------------------------------------
# Copyright (c) 2013 Polytechnique Montreal <www.neuro.polymtl.ca>
# Authors: Sara Dupont
# Modified: 2015-05-20
#
# About the license: see the file LICENSE.TXT
#########################################################################################
import sct_utils as sct
import os
import time
import sys
import getopt
from msct_parser import *
from msct_image import Image, get_dimension
from msct_multiatlas_seg import Model, SegmentationParam, GMsegSupervisedMethod
from msct_gmseg_utils import *


class Preprocessing:
    def __init__(self, target_fname, sc_seg_fname, tmp_dir='', t2_data=None, level_fname=None, denoising=True):

        # initiate de file names and copy the files into the temporary directory
        self.t2star = 'target.nii.gz'
        self.sc_seg = 'target_sc_seg.nii.gz'
        self.resample_to = 0.3

        if level_fname is not None:
            t2_data = None
            level_fname_nii = check_file_to_niigz(level_fname)
            if level_fname_nii:
                level_path, level_file_name, level_ext = sct.extract_fname(level_fname_nii)
                sct.run('cp ' + level_fname_nii + ' ' + tmp_dir + '/' + level_file_name + level_ext)
        else:
            level_path = level_file_name = level_ext = None

        if t2_data is not None:
            self.t2 = 't2.nii.gz'
            self.t2_seg = 't2_seg.nii.gz'
            self.t2_landmarks = 't2_landmarks.nii.gz'
        else:
            self.t2 = self.t2_seg = self.t2_landmarks = None

        sct.run('cp ' + target_fname + ' ' + tmp_dir + '/' + self.t2star)
        sct.run('cp ' + sc_seg_fname + ' ' + tmp_dir + '/' + self.sc_seg)
        if t2_data is not None:
            sct.run('cp ' + t2_data[0] + ' ' + tmp_dir + '/' + self.t2)
            sct.run('cp ' + t2_data[1] + ' ' + tmp_dir + '/' + self.t2_seg)
            sct.run('cp ' + t2_data[2] + ' ' + tmp_dir + '/' + self.t2_landmarks)

        # preprocessing
        os.chdir(tmp_dir)

        # resampling of the images
        nx, ny, nz, nt, self.original_px, self.original_py, pz, pt = get_dimension(Image(self.t2star))

        if round(self.original_px, 2) != self.resample_to or round(self.original_py, 2) != self.resample_to:
            self.t2star = resample_image(self.t2star, npx=self.resample_to, npy=self.resample_to)
            self.sc_seg = resample_image(self.sc_seg, binary=True, npx=self.resample_to, npy=self.resample_to)

        # denoising (optional)
        t2star_im = Image(self.t2star)
        if denoising:
            t2star_im.denoise_ornlm()
            t2star_im.save()
            self.t2star = t2star_im.file_name + t2star_im.ext

        '''
        status, t2_star_orientation = sct.run('sct_orientation -i ' + self.t2star)
        self.original_orientation = t2_star_orientation[4:7]
        '''
        self.original_orientation = t2star_im.orientation

        self.square_mask = crop_t2_star(self.t2star, self.sc_seg, box_size=int(22.5/self.resample_to))

        self.treated_target = sct.extract_fname(self.t2star)[1] + '_seg_in_croped.nii.gz'

        self.level_fname = None
        if t2_data is not None:
            self.level_fname = compute_level_file(self.t2star, self.sc_seg, self.t2, self.t2_seg, self.t2_landmarks)
        elif level_fname is not None:
            status, level_orientation = sct.run('sct_orientation -i ' + level_file_name + level_ext)
            # level_orientation = level_orientation[4:7]
            if level_orientation != 'IRP':
                status, level_orientation = sct.run('sct_orientation -i ' + level_file_name + level_ext + ' -s IRP')
                level_file_name += '_IRP'
            self.level_fname = level_file_name + level_ext

        os.chdir('..')


class FullGmSegmentation:

    def __init__(self, target_fname, sc_seg_fname, t2_data, level_fname, ref_gm_seg=None, model=None, compute_ratio=False, param=None):

        before = time.time()
        self.param = param
        sct.printv('\nBuilding the appearance model...', verbose=self.param.verbose, type='normal')
        if model is None:
            self.model = Model(model_param=self.param, k=0.8)
        else:
            self.model = model
        sct.printv('\n--> OK !', verbose=self.param.verbose, type='normal')

        self.target_fname = check_file_to_niigz(target_fname)
        self.sc_seg_fname = check_file_to_niigz(sc_seg_fname)
        self.t2_data = t2_data
        if level_fname is not None:
            self.level_fname = check_file_to_niigz(level_fname)
        else:
            self.level_fname = level_fname

        self.ref_gm_seg_fname = ref_gm_seg

        self.tmp_dir = 'tmp_' + sct.extract_fname(self.target_fname)[1] + '_' + time.strftime("%y%m%d%H%M%S")
        sct.run('mkdir ' + self.tmp_dir)

        self.gm_seg = None
        self.res_names = {}
        self.dice_name = None
        self.hausdorff_name = None

        self.segmentation_pipeline()

        if compute_ratio:
            self.compute_ratio()

        after = time.time()
        sct.printv('Done! (in ' + str(after-before) + ' sec) \nTo see the result, type :')
        if self.param.res_type == 'binary':
            wm_col = 'Red'
            gm_col = 'Blue'
            b = '0,1'
        else:
            wm_col = 'Blue-Lightblue'
            gm_col = 'Red-Yellow'
            b = '0.5,1'
        sct.printv('fslview ' + self.target_fname + ' ' + self.res_names['wm_seg'] + ' -l ' + wm_col + ' -t 0.4 -b ' + b + ' ' + self.res_names['gm_seg'] + ' -l ' + gm_col + ' -t 0.4  -b ' + b + ' &', param.verbose, 'info')

    # ------------------------------------------------------------------------------------------------------------------
    def segmentation_pipeline(self):
        sct.printv('\nDoing target pre-processing ...', verbose=self.param.verbose, type='normal')
        self.preprocessed = Preprocessing(self.target_fname, self.sc_seg_fname, tmp_dir=self.tmp_dir, t2_data=self.t2_data, level_fname=self.level_fname, denoising=self.param.target_denoising)

        os.chdir(self.tmp_dir)

        if self.preprocessed.level_fname is not None:
            self.level_to_use = self.preprocessed.level_fname
        else:
            self.level_to_use = None

        sct.printv('\nDoing target gray matter segmentation ...', verbose=self.param.verbose, type='normal')
        self.gm_seg = GMsegSupervisedMethod(self.preprocessed.treated_target, self.level_to_use, self.model, gm_seg_param=self.param)

        if self.ref_gm_seg_fname is not None:
            os.chdir('..')
            sct.run('cp ' + self.ref_gm_seg_fname + ' ' + self.tmp_dir + '/' + sct.extract_fname(self.ref_gm_seg_fname)[1] + sct.extract_fname(self.ref_gm_seg_fname)[2])
            sct.run('cp ' + self.sc_seg_fname + ' ' + self.tmp_dir + '/' + sct.extract_fname(self.sc_seg_fname)[1] + sct.extract_fname(self.sc_seg_fname)[2])
            os.chdir(self.tmp_dir)

            sct.printv('Computing Dice coefficient and Hausdorff distance ...', verbose=self.param.verbose, type='normal')
            self.dice_name, self.hausdorff_name = self.validation()

        sct.printv('\nDoing result post-processing ...', verbose=self.param.verbose, type='normal')
        self.post_processing()

        os.chdir('..')

    # ------------------------------------------------------------------------------------------------------------------
    def post_processing(self):
        square_mask = Image(self.preprocessed.square_mask)
        tmp_res_names = []
        for res_im in [self.gm_seg.res_wm_seg, self.gm_seg.res_gm_seg, self.gm_seg.corrected_wm_seg]:
            res_im_original_space = inverse_square_crop(res_im, square_mask)
            res_im_original_space.save()
            sct.run('sct_orientation -i ' + res_im_original_space.file_name + '.nii.gz -s ' + self.preprocessed.original_orientation)
            res_name = sct.extract_fname(self.target_fname)[1] + res_im.file_name[len(sct.extract_fname(self.preprocessed.treated_target)[1]):] + '.nii.gz'

            if self.param.res_type == 'binary':
                bin = True
            else:
                bin = False
            old_res_name = resample_image(res_im_original_space.file_name + '_RPI.nii.gz', npx=self.preprocessed.original_px, npy=self.preprocessed.original_py, binary=bin)

            if self.param.res_type == 'prob':
                # sct.run('fslmaths ' + old_res_name + ' -thr 0.05 ' + old_res_name)
                # WARNING: until sct_maths -thr option is changed, this will output a binary segmentation
                sct.run('sct_maths -i ' + old_res_name + ' -thr 0.05 -o ' + old_res_name)

            sct.run('cp ' + old_res_name + ' ../' + res_name)

            tmp_res_names.append(res_name)
        self.res_names['wm_seg'] = tmp_res_names[0]
        self.res_names['gm_seg'] = tmp_res_names[1]
        self.res_names['corrected_wm_seg'] = tmp_res_names[2]

    # ------------------------------------------------------------------------------------------------------------------
    def compute_ratio(self):
        sct.run('sct_process_segmentation -i '+self.res_names['gm_seg']+' -p csa -o gm_csa ')
        sct.run('mv csa.txt gm_csa.txt')

        sct.run('sct_process_segmentation -i '+self.res_names['corrected_wm_seg']+' -p csa -o wm_csa ')
        sct.run('mv csa.txt wm_csa.txt')

        gm_csa = open('gm_csa.txt', 'r')
        wm_csa = open('wm_csa.txt', 'r')

        ratio = open('ratio.txt', 'w')

        gm_lines = gm_csa.readlines()
        wm_lines = wm_csa.readlines()

        gm_csa.close()
        wm_csa.close()

        for gm_line, wm_line in zip(gm_lines, wm_lines):
            i, gm_area = gm_line.split(',')
            j, wm_area = wm_line.split(',')
            assert i == j
            ratio.write(i+','+str(float(gm_area)/float(wm_area))+'\n')

        ratio.close()



    # ------------------------------------------------------------------------------------------------------------------
    def validation(self):
        ext = '.nii.gz'
        validation_dir = 'validation'
        sct.run('mkdir ' + validation_dir)

        # loading the images
        im_ref_gm_seg = Image(sct.extract_fname(self.ref_gm_seg_fname)[1] + ext)
        im_ref_wm_seg = inverse_gmseg_to_wmseg(im_ref_gm_seg, Image(sct.extract_fname(self.sc_seg_fname)[1] + ext), im_ref_gm_seg.file_name, save=False)

        res_gm_seg_bin = self.gm_seg.res_gm_seg.copy()
        res_wm_seg_bin = self.gm_seg.res_wm_seg.copy()

        if self.param.res_type == 'prob':
            res_gm_seg_bin.data = np.asarray((res_gm_seg_bin.data >= 0.5).astype(int))
            res_wm_seg_bin.data = np.asarray((res_wm_seg_bin.data >= 0.50001).astype(int))

        mask = Image(self.preprocessed.square_mask)

        # doing the validation
        os.chdir(validation_dir)

        im_ref_gm_seg.path = './'
        im_ref_gm_seg.file_name = 'ref_gm_seg'
        im_ref_gm_seg.ext = ext
        im_ref_gm_seg.save()
        ref_gm_seg_new_name = resample_image(im_ref_gm_seg.file_name + ext, npx=self.preprocessed.resample_to, npy=self.preprocessed.resample_to, binary=True)
        im_ref_gm_seg = Image(ref_gm_seg_new_name)
        sct.run('rm ' + ref_gm_seg_new_name)

        im_ref_wm_seg.path = './'
        im_ref_wm_seg.file_name = 'ref_wm_seg'
        im_ref_wm_seg.ext = ext
        im_ref_wm_seg.save()
        ref_wm_seg_new_name = resample_image(im_ref_wm_seg.file_name + ext, npx=self.preprocessed.resample_to, npy=self.preprocessed.resample_to, binary=True)
        im_ref_wm_seg = Image(ref_wm_seg_new_name)
        sct.run('rm ' + ref_wm_seg_new_name)

        ref_orientation = im_ref_gm_seg.orientation
        im_ref_gm_seg.change_orientation('IRP')
        im_ref_wm_seg.change_orientation('IRP')

        im_ref_gm_seg.crop_from_square_mask(mask, save=False)
        im_ref_wm_seg.crop_from_square_mask(mask, save=False)

        im_ref_gm_seg.change_orientation('RPI')
        im_ref_wm_seg.change_orientation('RPI')

        # saving the images to call the validation functions
        res_gm_seg_bin.path = './'
        res_gm_seg_bin.file_name = 'res_gm_seg_bin'
        res_gm_seg_bin.ext = ext
        res_gm_seg_bin.save()

        res_wm_seg_bin.path = './'
        res_wm_seg_bin.file_name = 'res_wm_seg_bin'
        res_wm_seg_bin.ext = ext
        res_wm_seg_bin.save()

        im_ref_gm_seg.path = './'
        im_ref_gm_seg.file_name = 'ref_gm_seg'
        im_ref_gm_seg.ext = ext
        im_ref_gm_seg.save()

        im_ref_wm_seg.path = './'
        im_ref_wm_seg.file_name = 'ref_wm_seg'
        im_ref_wm_seg.ext = ext
        im_ref_wm_seg.save()

        sct.run('sct_orientation -i ' + res_gm_seg_bin.file_name + ext + ' -s RPI')
        res_gm_seg_bin.file_name += '_RPI'
        sct.run('sct_orientation -i ' + res_wm_seg_bin.file_name + ext + ' -s RPI')
        res_wm_seg_bin.file_name += '_RPI'

        res_gm_seg_bin = Image(res_gm_seg_bin.file_name + ext)
        im_ref_gm_seg.hdr.set_zooms(res_gm_seg_bin.hdr.get_zooms())  # correcting the pix dimension
        im_ref_gm_seg.save()

        res_wm_seg_bin = Image(res_wm_seg_bin.file_name + ext)
        im_ref_wm_seg.hdr.set_zooms(res_wm_seg_bin.hdr.get_zooms())  # correcting the pix dimension
        im_ref_wm_seg.save()

        # Dice
        try:
            status_gm, output_gm = sct.run('sct_dice_coefficient ' + im_ref_gm_seg.file_name + ext + ' ' + res_gm_seg_bin.file_name + ext + '  -2d-slices 2', error_exit='warning', raise_exception=True)
        except Exception:
            # put the result and the reference in the same space using a registration with ANTs with no iteration:
            sct.run('isct_antsRegistration -d 3 -t Translation[0] -m MI['+res_gm_seg_bin.file_name+ext+','+im_ref_gm_seg.file_name+ext+',1,16] -o [reg_ref_to_res,'+im_ref_gm_seg.file_name+'_in_res_space'+ext+'] -n BSpline[3] -c 0 -f 1 -s 0')
            sct.run('sct_maths -i ' + im_ref_gm_seg.file_name + '_in_res_space' + ext + ' -thr 0.1 -o ' + im_ref_gm_seg.file_name + '_in_res_space' + ext)
            sct.run('sct_maths -i ' + im_ref_gm_seg.file_name + '_in_res_space' + ext + ' -bin -o ' + im_ref_gm_seg.file_name + '_in_res_space' + ext )
            status_gm, output_gm = sct.run('sct_dice_coefficient ' + im_ref_gm_seg.file_name + '_in_res_space' + ext + ' ' + res_gm_seg_bin.file_name + ext + '  -2d-slices 2', error_exit='warning')
        try:
            status_wm, output_wm = sct.run('sct_dice_coefficient ' + im_ref_wm_seg.file_name + ext + ' ' + res_wm_seg_bin.file_name + ext + '  -2d-slices 2', error_exit='warning', raise_exception=True)
        except Exception:
            # put the result and the reference in the same space using a registration with ANTs with no iteration:
            sct.run('isct_antsRegistration -d 3 -t Translation[0] -m MI['+res_wm_seg_bin.file_name+ext+','+im_ref_wm_seg.file_name+ext+',1,16] -o [reg_ref_to_res,'+im_ref_wm_seg.file_name+'_in_res_space'+ext+'] -n BSpline[3] -c 0 -f 1 -s 0')
            sct.run('sct_maths -i ' + im_ref_wm_seg.file_name + '_in_res_space' + ext + ' -thr 0.1 -o ' + im_ref_wm_seg.file_name + '_in_res_space' + ext)
            sct.run('sct_maths -i ' + im_ref_wm_seg.file_name + '_in_res_space' + ext + ' -bin -o ' + im_ref_wm_seg.file_name + '_in_res_space' + ext)
            status_wm, output_wm = sct.run('sct_dice_coefficient ' + im_ref_wm_seg.file_name + '_in_res_space' + ext + ' ' + res_wm_seg_bin.file_name + ext + '  -2d-slices 2', error_exit='warning')

        dice_name = 'dice_' + sct.extract_fname(self.target_fname)[1] + '_' + self.param.res_type + '.txt'
        dice_fic = open('../../' + dice_name, 'w')
        if self.param.res_type == 'prob':
            dice_fic.write('WARNING : the probabilistic segmentations were binarized with a threshold at 0.5 to compute the dice coefficient \n')
        dice_fic.write('\n--------------------------------------------------------------\n'
                       'Dice coefficient on the Gray Matter segmentation:\n')
        dice_fic.write(output_gm)
        dice_fic.write('\n\n--------------------------------------------------------------\n'
                       'Dice coefficient on the White Matter segmentation:\n')
        dice_fic.write(output_wm)
        dice_fic.close()
        # sct.run(' mv ./' + dice_name + ' ../')

        hd_name = 'hd_' + sct.extract_fname(self.target_fname)[1] + '_' + self.param.res_type + '.txt'
        sct.run('sct_compute_hausdorff_distance.py -i ' + res_gm_seg_bin.file_name + ext + ' -r ' + im_ref_gm_seg.file_name + ext + ' -t 1 -o ' + hd_name + ' -v ' + str(self.param.verbose))
        sct.run('mv ./' + hd_name + ' ../../')

        os.chdir('..')
        return dice_name, hd_name


########################################################################################################################
# ------------------------------------------------------  MAIN ------------------------------------------------------- #
########################################################################################################################

if __name__ == "__main__":
    param = SegmentationParam()
    input_target_fname = None
    input_sc_seg_fname = None
    input_t2_data = None
    input_level_fname = None
    input_ref_gm_seg = None
    compute_ratio = False
    if param.debug:
        print '\n*** WARNING: DEBUG MODE ON ***\n'
        fname_input = param.path_model + "/errsm_34.nii.gz"
        fname_input = param.path_model + "/errsm_34_seg_in.nii.gz"
    else:
        param_default = SegmentationParam()

        # Initialize the parser
        parser = Parser(__file__)
        parser.usage.set_description('Segmentation of the white/gray matter on a T2star or MT image\n'
                                     'Multi-Atlas based method: the model containing a template of the white/gray matter segmentation along the cervical spinal cord, and a PCA space to describe the variability of intensity in that template is provided in the toolbox. ')
        parser.add_option(name="-i",
                          type_value="file",
                          description="Target image to segment",
                          mandatory=True,
                          example='t2star.nii.gz')
        parser.add_option(name="-s",
                          type_value="file",
                          description="Spinal cord segmentation of the target",
                          mandatory=True,
                          example='sc_seg.nii.gz')
        parser.usage.addSection('STRONGLY RECOMMENDED ARGUMENTS\n'
                                'Choose one of them')
        parser.add_option(name="-l",
                          type_value="str",
                          description="Image containing level labels for the target or str indicating the level (if the target has only one slice)"
                                      "If -l is used, no need to provide t2 data",
                          mandatory=False,
                          example='MNI-Poly-AMU_level_IRP.nii.gz')
        parser.add_option(name="-t2",
                          type_value=[[','], 'file'],
                          description="T2 data associated to the input image : used to register the template on the T2star and get the vertebral levels\n"
                                      "In this order, without whitespace : t2_image,t2_sc_segmentation,t2_landmarks\n(see: http://sourceforge.net/p/spinalcordtoolbox/wiki/create_labels/)",
                          mandatory=False,
                          default_value=None,
                          example='t2.nii.gz,t2_seg.nii.gz,landmarks.nii.gz')
        parser.usage.addSection('SEGMENTATION OPTIONS')
        parser.add_option(name="-use-levels",
                          type_value='multiple_choice',
                          description="Use the level information for the model or not",
                          mandatory=False,
                          default_value=1,
                          example=['0', '1'])
        parser.add_option(name="-weight",
                          type_value='float',
                          description="weight parameter on the level differences to compute the similarities (beta)",
                          mandatory=False,
                          default_value=2.5,
                          example=2.0)
        parser.add_option(name="-denoising",
                          type_value='multiple_choice',
                          description="1: Adaptative denoising from F. Coupe algorithm, 0: no  WARNING: It affects the model you should use (if denoising is applied to the target, the model should have been coputed with denoising too",
                          mandatory=False,
                          default_value=1,
                          example=['0', '1'])
        parser.add_option(name="-normalize",
                          type_value='multiple_choice',
                          description="Normalization of the target image's intensity using median intensity values of the WM and the GM, recomended with MT images or other types of contrast than T2*",
                          mandatory=False,
                          default_value=1,
                          example=['0', '1'])
        parser.add_option(name="-medians",
                          type_value=[[','], 'float'],
                          description="Median intensity values in the target white matter and gray matter (separated by a comma without white space)\n"
                                      "If not specified, the mean intensity values of the target WM and GM  are estimated automatically using the dictionary average segmentation by level.\n"
                                      "Only if the -normalize flag is used",
                          mandatory=False,
                          default_value=None,
                          example=["450,540"])
        parser.add_option(name="-model",
                          type_value="folder",
                          description="Path to the model data",
                          mandatory=False,
                          example='/home/jdoe/gm_seg_model_data/')
        parser.usage.addSection('OUTPUT OTIONS')
        parser.add_option(name="-res-type",
                          type_value='multiple_choice',
                          description="Type of result segmentation : binary or probabilistic",
                          mandatory=False,
                          default_value='prob',
                          example=['binary', 'prob'])
        parser.add_option(name="-ratio",
                          description="Compute GM/WM ratio",
                          mandatory=False)
        parser.add_option(name="-o",
                          type_value="str",
                          description="output name for the results",
                          mandatory=False,
                          example='t2star_res.nii.gz')
        parser.usage.addSection('MISC')
        parser.add_option(name="-ref",
                          type_value="file",
                          description="Reference segmentation of the gray matter for segmentation validation (outputs Dice coefficient and Hausdoorff's distance)",
                          mandatory=False,
                          example='manual_gm_seg.nii.gz')
        parser.add_option(name="-v",
                          type_value="int",
                          description="verbose: 0 = nothing, 1 = classic, 2 = expended",
                          mandatory=False,
                          default_value=0,
                          example='1')

        arguments = parser.parse(sys.argv[1:])
        input_target_fname = arguments["-i"]
        input_sc_seg_fname = arguments["-s"]
        if "-model" in arguments:
            param.path_model = arguments["-model"]
        param.todo_model = 'load'

        if "-o" in arguments:
            param.output_name = arguments["-o"]
        if "-t2" in arguments:
            input_t2_data = arguments["-t2"]
        if "-l" in arguments:
            input_level_fname = arguments["-l"]
        if "-use-levels" in arguments:
            param.use_levels = bool(int(arguments["-use-levels"]))
        if "-weight" in arguments:
            param.weight_gamma = arguments["-weight"]
        if "-denoising" in arguments:
            param.target_denoising = bool(int(arguments["-denoising"]))
        if "-normalize" in arguments:
            param.target_normalization = bool(int(arguments["-normalize"]))
        if "-means" in arguments:
            param.target_means = arguments["-means"]

        if "-ratio" in arguments:
            compute_ratio = True
        if "-res-type" in arguments:
            param.res_type = arguments["-res-type"]
        if "-ref" in arguments:
            input_ref_gm_seg = arguments["-ref"]
        if "-v" in arguments:
            param.verbose = arguments["-v"]

        if input_level_fname is None and input_t2_data is None:
            param.use_levels = False
            param.weight_gamma = 0

    gmsegfull = FullGmSegmentation(input_target_fname, input_sc_seg_fname, input_t2_data, input_level_fname, ref_gm_seg=input_ref_gm_seg, compute_ratio=compute_ratio, param=param)
