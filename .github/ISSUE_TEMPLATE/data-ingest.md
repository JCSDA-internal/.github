---
name: Data ingest request
about: Use this template for ingesting data into r2d2
title: "[Ingest]"
labels: ''
assignees: ''

---

## Ingest description

**Instructions:** Please provide values for the following entries and set the experiment lifetime for the data to be ingested to "science" to avoid it being scrubbed (see instructions at the end of this template for more information). Then delete this line.

Description: 

Data type (_For example: observation, forecast_):

Other metadata (_For example: provider, observation_type, model_): 

Timeline (_When does this issue need to be completed_):

## Data quality certification

- [ ] I certify that this data set has been checked for errors (including malformed or missing data) and tested using Skylab across the entire ingest data range. 

## Date range

Start date:

End date:

## Source host and data store

Source host: 

Source data store:

## Target data store type (select one)

- [ ] archive
- [ ] experiments
- [ ] gfsensemble
- [ ] geosensemble
- [ ] other 
    - Please describe: 

## Data ingest processing checklist for the R2D2 Administrator

Data has been ingested into the target data store type on the following data hubs:

- [ ] ~~AWS ParallelCluster R&D~~ (temporarily suspended)
- [ ] Casper/Derecho (NCAR-Wyoming Supercomputing Center)
- [ ] Discover (NASA Center for Climate Simulation)
- [ ] Orion/Hercules (Mississippi State University High Performance Computing)
- [ ] S3 (jcsda-usaf-aws-us-east-2)
- [ ] S4 (Space Science and Engineering Center)
- [ ] NOAA ParallelWorks Gcloud

## Instructions for changing the lifetime of an experiment

This changes the lifetime of experiment `b00b7f` from the default `debug` to `science`:
```
from r2d2 import R2D2Index
R2D2Index.update(item='experiment', name='b00b7f', key='lifetime', value='science')
```
