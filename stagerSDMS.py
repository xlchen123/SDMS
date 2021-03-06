#!/usr/bin/env python
b'This script requires python 3.4'

"""
Stager which reads staging file and sets stageMarker in HPSS_<Target> collection

The staging file can have several sets,

each set can have the following parameters

 'stageTarget'   : 'XRD'           (For now the only option)
    as in listOfStageTargets = ['XRD', 'Disk']

 'target': 'picoDst'       (For now the only option)
    as in  listOfTargets = ['picoDst', 'picoDstJet', 'aschmah']

 Data set parameters:
    as in listOfQueryItems = ['runyear', 'system', 'energy', 'trigger', 'production', 'day', 'runnumber', 'stream']

 with example values:
 'runyear': 'Run10',
 'system': 'AuAu',
 'energy': '11GeV',
 'trigger': 'all',
 'production': 'P10ih',
 'day': 149,
 'runnumber': 11149081,
 'stream': 'st_physics_adc',
"""

import sys
import os
import re
import json

import logging as log
import time
import socket
import datetime
import shlex, subprocess

from mongoUtil import mongoDbUtil
import pymongo

from pymongo import results
from pymongo import errors
from pymongo import bulk

from pprint import pprint

##############################################
# -- GLOBAL CONSTANTS

SCRATCH_SPACE = "/scratch"

##############################################

# -- Check for a proper Python Version
if sys.version[0:3] < '3.0':
    print ('Python version 3.0 or greater required (found: {0}).'.format(sys.version[0:5]))
    sys.exit(-1)

# ----------------------------------------------------------------------------------
class stagerSDMS:
    """ Stager to from HPSS at NERSC"""

    # _________________________________________________________
    def __init__(self, stageingFile, scratchSpace):
        self._stageingFile = stageingFile
        self._scratchSpace = scratchSpace

        self._listOfStageTargets = ['XRD', 'Disk']

        self._listOfQueryItems   = ['runyear', 'system', 'energy',
                                    'trigger', 'production', 'day',
                                    'runnumber', 'stream']

        self._listOfTargets = ['picoDst', 'picoDstJet', 'aschmah']

        # -- base Collection Names
        self._baseColl = {'picoDst': 'PicoDsts',
                          'picoDstJet': 'PicoDstsJets',
                          'aschmah': 'ASchmah'}

        self._readStagingFile()

        self._addCollections(dbUtil)

    # _________________________________________________________
    def _readStagingFile(self):
        """Read in staging file."""

        with open(self._stageingFile) as dataFile:
            setList = json.load(dataFile)

            try:
                self._sets = setList['sets']
            except:
                print('Error reading staging file: no "sets" found')
                sys.exit(-1)

    # _________________________________________________________
    def _addCollections(self, dbUtil):
        """Get collections from mongoDB."""

        self._collsHPSS = dict.fromkeys(self._listOfTargets)
        for target in self._listOfTargets:
            self._collsHPSS[target] = dbUtil.getCollection('HPSS_' + self._baseColl[target])

        self._collsStage = dict.fromkeys(self._listOfTargets)
        for target in self._listOfTargets:
            self._collsStage[target] = dict.fromkeys(self._listOfStageTargets)

            for stageTarget in self._listOfStageTargets:
                self._collsStage[target][stageTarget] = dbUtil.getCollection(stageTarget+'_'+ self._baseColl[target])

    # _________________________________________________________
    def markFilesToBeStaged(self):
        """Mark files to be staged in staging file."""

        self._resetAllStagingMarks()

        for stageSet in self._sets:
            if not self._prepareSet(stageSet):
                continue
            self._coll.update_many(stageSet, {'$set': {self._targetField: True}})

    # _________________________________________________________
    def _resetAllStagingMarks(self):
        """Reset all staging marks."""

        for target, collections in self._collStage.items():
            for stageTarget, coll in collections.items():
                targetField = 'staging.stageMarker{0}'.format(stageTarget)

                coll.update_many({}, {'$set': {targetField: False}})

    # _________________________________________________________
    def listOfFilesToBeStaged(self):
        """Returns a list of all files to be staged"""

        for target, collections in self._collStage.items():
            for stageTarget, coll in collections.items():
                targetField = 'staging.stageMarker{0}'.format(stageTarget)
                nStaged = coll.find({targetField: True}).count()

                print('For {0} in collection: {1}'.format(target, coll.name))
                print('   Files to be staged on {0}: {1}'.format(stageTarget, nStaged))

    # _________________________________________________________
    def _prepareSet(self, stageSet):
        """Prepare set to be staged."""

        # -- Check for stageTarget
        try:
            stageTarget = stageSet['stageTarget']
            if stageTarget not in  self._listOfStageTargets:
                print('Error reading staging file: Unknown "stageTarget"', stageTarget)
                return False
            self._targetField = "staging.stageMarker{0}".format(stageTarget)

        except:
            print('Error reading staging file: no "stageTarget" found in set' , stageSet)
            stageTarget = None
            return False

        # -- Check for target
        try:
            target = stageSet['target']
            if target not in  self._listOfTargets:
                print('Error reading staging file: Unknown "target"', target)
                return False
            self._coll = self._collsStage[target][stageTarget]

        except:
            print('Error reading staging file: no "target" found in set' , stageSet)
            self.target = None
            return False

        # -- Clean up
        del(stageSet['target'])
        del(stageSet['stageTarget'])

        # -- Check if query items are correct
        for key, value in stageSet.items():
            if "starDetails." in key:
                continue
            if key not in self._listOfQueryItems:
                print('Error reading staging file: Query item does not exist:', key, value)
                return False
            del(stageSet[key])
            starKey = 'starDetails.' + key
            stageSet[starKey] = value

        return True

    # _________________________________________________________
    def prepareListOfFilesToBeStaged(self, stageTarget):
        """Check for files to be staged"""

        if stageTarget not in  self._listOfStageTargets:
            return False

        stageField = 'staging.stageMarker{0}'.format(stageTarget)

        # -- Loop over targets
        for target in self._listOfTargets:

            # -- Get all files to be staged
            #   ... FIX query
            hpssDocs = self._collsHPSS[target].find({'dataClass': target, stageField: True})])
            docsSetHPSS = set([item['filePath'] for item in hpssDocs])

            # -- Get all files on stageing Target
            stagedDocs = list(self._collsXRDNew[target].find({'storage.location': stageTarget,
                                                      'dataClass': target}))
            docsSetStaged = set([item['filePath'] for item in stagedDocs])

            # -- Document to be staged
            docsToStage  = docsSetHPSS - docsSetStaged

            # -- Documents to be removed from stageTarget
            docsToRemove = docsSetStaged - docsSetHPSS

            # -- Mark Documents as to be unStaged
            self._collsStage[target][stageTarget].update_many({'filePath' : '$in' : docsToRemove},
                                                              { '$set': {'unStageFlag': True} })


            # -- Make list of documents to be removed
            mark collection files in staged collection as  to be removed
            -> use other clear script to explictly remove

            listToStageFromHPSS = []

            #            get files  which are not in tar file
            #                -> make list of files to be stage
            #                    - get files from HPSS to staging area
            #- use hpss tape ordering


            #get list file in tar balls  -> sort by tar file name
            #            -> disti   nct -> via set

#    loop over tarballs and get nFiles per tar ball
#    if nFiles is larger then 25%
#        (get all files from Tarball into stageingArea)
#        add tar ball to stage list -> (use hpss tapeordering on it)


            self._stageHPSSFiles(listToStageFromHPSS)

    #  ____________________________________________________________________________
    def _stageHPSSFiles(self, stageList):
        """ Stage list of files from HPSS on to scratch space"""

        foo = "dd"
        #-> tape ordering
        #-> stage files ->

    # ____________________________________________________________________________
    def stage(self):
        """Stage all files from stageing area to staging location"""

# ____________________________________________________________________________
def main():
    """Initialize and run"""

    # -- Connect to mongoDB
    dbUtil = mongoDbUtil("", "admin")

    stager = stagerSDMS(dbUtil, 'stagingRequest.json', os.getenv('SCRATCH', SCRATCH_SPACE))

    # -- Mark files to be staged
    stager.markFilesToBeStaged()
    stager.listOfFilesToBeStaged()

    # -- Get list of files to be staged
    stager.prepareListOfFilesToBeStaged()

    # -- Stage from staging area to staging location
    stager.stage()

    dbUtil.close()
# ____________________________________________________________________________
if __name__ == "__main__":
    print("Start SDMS Stager!")
    sys.exit(main())
