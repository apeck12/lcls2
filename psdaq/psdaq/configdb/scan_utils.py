import pprint
pp = pprint.PrettyPrinter(indent=4)

#
#  scan_utils.py
#
def copy_config_entry(d,old,key):
    o = old
    keys = key.split('.')
    for k in keys[:-1]:
        if k in o:
            o = o[k]
        else:
            print('Error in lookup of [:types:]{:}'.format(key))
            raise KeyError
        if k in d:
            d = d[k]
        else:
            d[k] = {}
            d = d[k]
    try:
        d[keys[-1]] = o[keys[-1]]
    except:
        pp.pprint(o.keys())
        pp.pprint(d.keys())
        print('Caught exception on {}'.format(key))
        raise KeyError(keys[-1])

def update_config_entry(r,old,update):
    # Still need special handling for enums
    if not ':types:' in r:
        r[':types:'] = {}
    for key in update.keys():
        if key == 'step_docstring':
            r[':types:'][key] = 'CHARSTR'
        elif key == 'step_value':
            r[':types:'][key] = 'FLOAT'
        else:
            copy_config_entry(r[':types:'],old[':types:'],key)

    for key,value in update.items():
        d = r
        keys = key.split('.')
        for k in keys[:-1]:
            if k in d:
                d = d[k]
            else:
                d[k] = {}
                d = d[k]
        d[keys[-1]] = value
        sval = '{:}'.format(value)
        if len(sval)<64:
            print('[{:}] : {:}'.format(key,sval))
        else:
            print('[{:}] : (truncated)'.format(key))
        

def copy_reconfig_keys(r,old,update):
    # Still need special handling for enums
    if not ':types:' in r:
        r[':types:'] = {}
    for key in update:
        if key == 'step_docstring':
            r[':types:'][key] = 'CHARSTR'
        elif key == 'step_value':
            r[':types:'][key] = 'FLOAT'
        else:
            copy_config_entry(r[':types:'],old[':types:'],key)

    for key in update:
        if key == 'step_docstring':
            r[key] = ''
        elif key == 'step_value':
            r[key] = 0.
        else:
            copy_config_entry(r,old,key)

