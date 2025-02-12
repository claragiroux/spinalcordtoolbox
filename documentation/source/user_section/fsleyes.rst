.. _fsleyes_integration:

*******************
FSLeyes Integration
*******************

`FSLeyes <https://fsl.fmrib.ox.ac.uk/fsl/fslwiki/FSLeyes#Install_as_part_of_FSL_.28recommended.29>`_
is part of the larger `FSL <https://fsl.fmrib.ox.ac.uk/fsl/fslwiki>`_ package, which is a library
containing tools for FMRI, MRI, and DTI brain imaging data. ``FSLeyes`` is the image viewer for this package, and can
be installed as either part of the ``FSL`` package, or as a standalone app. See
:ref:`fsleyes_installation` for instructions on how to install.


SCT + FSLeyes
=============

``SCT`` has a plugin script that can be used with the ``FSLeyes`` interface (GUI).

To enable the ``SCT`` plugin:

1. Open ``FSLeyes`` application.
2. ``File`` -> ``Run script``
3. Select the script ``spinalcordtoolbox/contrib/fsl_integration/sct_plugin.py``

You should see something like this appear in the ``FSLeyes`` interface:

.. image:: ../../imgs/sct_fsleyes.png
  :alt: SCT-FSLeyes Interface
