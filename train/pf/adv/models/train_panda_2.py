#!/usr/local/bin/python2.7

from sys import exit, stdout, argv
from os import environ, system
environ['KERAS_BACKEND'] = 'tensorflow'

import numpy as np
import utils
import signal

from keras.layers import Input, Dense, Dropout, concatenate, LSTM, BatchNormalization, Conv1D, concatenate
from keras.models import Model 
from keras.callbacks import ModelCheckpoint, LambdaCallback, TensorBoard
from keras.optimizers import Adam, SGD
from keras.utils import np_utils
from keras import backend as K
K.set_image_data_format('channels_last')

from adversarial import Adversary
import obj 
import config 
#config.DEBUG = True

#config.n_truth = 5
#config.truth = 'resonanceType'
#config.adversary_mask = 0

''' 
some global definitions
''' 
#obj.limit = 50

LEARNMASS = True
LEARNRHO = False
LEARNPT = True

DECORRMASS = True
DECORRRHO = False
DECORRPT = False

adv_loss_weights = [0.0001, 100]

ADV = 0
NEPOCH = 10
APOSTLE = 'panda_2'

system('cp %s models/train_%s.py'%(argv[0], APOSTLE))


''' 
instantiate data loaders 
''' 

def make_coll(fpath):
    coll = obj.PFSVCollection()
    coll.add_categories(['singletons', 'pf'], fpath) 
    return coll 

top = make_coll('/fastscratch/snarayan/pandaarrays/v1//PARTITION/Top_*_CATEGORY.npy')
qcd = make_coll('/fastscratch/snarayan/pandaarrays/v1//PARTITION/QCD_*_CATEGORY.npy')

data = [top, qcd]

# preload some data just to get the dimensions
if obj.limit is None:
    data[0].objects['train']['pf'].load(memory=False)
    dims = data[0].objects['train']['pf'].data.data.shape 
else:
    dims = (None, obj.limit, 9) # override

'''
first build the classifier!
'''

# set up data 
opts = {'learn_mass':LEARNMASS, 
        'learn_pt':LEARNPT, 
        'learn_rho':LEARNRHO}
classifier_train_gen = obj.generatePF(data, partition='train', batch=5000, normalize=True, **opts)
classifier_validation_gen = obj.generatePF(data, partition='validate', batch=10000, normalize=True, **opts)
classifier_test_gen = obj.generatePF(data, partition='test', batch=2, **opts)
test_i, test_o, test_w = next(classifier_test_gen)
#print test_i

inputs  = Input(shape=(dims[1], dims[2]), name='input')
mass_inputs = Input(shape=(1,), name='mass_input')
rho_inputs = Input(shape=(1,), name='rho_input')
pt_inputs = Input(shape=(1,), name='pt_input')
norm    = BatchNormalization(momentum=0.6, name='input_bnorm')                              (inputs)
conv = Conv1D(32, 2, activation='relu', name='conv0', kernel_initializer='lecun_uniform', padding='same')(norm)
norm    = BatchNormalization(momentum=0.6, name='conv0_bnorm')                              (conv)
conv = Conv1D(16, 4, activation='relu', name='conv1', kernel_initializer='lecun_uniform', padding='same')(norm)
norm    = BatchNormalization(momentum=0.6, name='conv1_bnorm')                              (conv)
lstm    = LSTM(100, go_backwards=True, implementation=2, name='lstm')                       (norm)
norm    = BatchNormalization(momentum=0.6, name='lstm_norm')                                (lstm)
drop    = Dropout(0.1)                                                                      (norm)
dense   = Dense(100, activation='relu',name='lstmdense',kernel_initializer='lecun_uniform') (drop)
norm    = BatchNormalization(momentum=0.6,name='lstmdense_norm')                            (dense)
for i in xrange(1,5):
    dense = Dense(50, activation='relu',name='dense%i'%i)(norm)
    norm = BatchNormalization(momentum=0.6,name='dense%i_norm'%i)(dense)

if LEARNMASS or LEARNPT or LEARNRHO:
    to_merge = [norm]
    if LEARNMASS:
        to_merge.append(mass_inputs)
    if LEARNRHO:
        to_merge.append(rho_inputs)
    if LEARNPT:
        to_merge.append(pt_inputs)
    merge    = concatenate(to_merge)
    dense = Dense(50, activation='tanh', name='dense5a')(merge)
    norm = BatchNormalization(momentum=0.6,name='dense5a_norm')(dense)
    # dense = Dense(50, activation='tanh', name='dense5')(norm)
    # norm = BatchNormalization(momentum=0.6,name='dense5_norm')(dense)
else:
    dense = Dense(50, activation='tanh',name='dense5')(norm)
    norm = BatchNormalization(momentum=0.6,name='dense5_norm')(dense)

y_hat   = Dense(config.n_truth, activation='softmax')                                       (norm)

i = [inputs]
if LEARNMASS:
    i.append(mass_inputs)
if LEARNRHO:
    i.append(rho_inputs)
if LEARNPT:
    i.append(pt_inputs)
classifier = Model(inputs=i, outputs=y_hat)
classifier.compile(optimizer=Adam(lr=0.01),
                   loss='categorical_crossentropy',
                   metrics=['accuracy'])

# print '########### CLASSIFIER ############'
# classifier.summary()
# print '###################################'

pred = classifier.predict(test_i)


# ctrl+C now triggers a graceful exit
def save_classifier(name='classifier_conv', model=classifier):
    model.save('models/%s_%s.h5'%(name, APOSTLE))

def save_and_exit(signal=None, frame=None, name='classifier_conv', model=classifier):
    save_classifier(name, model)
    flog.close()
    exit(1)

signal.signal(signal.SIGINT, save_and_exit)


''' 
now build the adversarial setup
'''

# set up data 
opts = {'decorr_mass':DECORRMASS, 
        'decorr_rho':DECORRRHO, 
        'decorr_pt':DECORRPT, 
        'learn_mass':LEARNMASS, 
        'learn_pt':LEARNPT, 
        'learn_rho':LEARNRHO}
train_gen = obj.generatePF(data, partition='train', batch=1000, normalize=False, **opts)
validation_gen = obj.generatePF(data, partition='validate', batch=1000, normalize=True, **opts)
test_gen = obj.generatePF(data, partition='test', batch=1, **opts)

# build the model 
kin_hats = Adversary(config.n_decorr_bins, n_outputs=(int(DECORRMASS)+int(DECORRPT)+int(DECORRRHO)), scale=0.0001)(y_hat)
# kin_hats = Adversary(config.n_decorr_bins, n_outputs=2, scale=0.01)(y_hat)

i = [inputs]
if LEARNMASS:
    i.append(mass_inputs)
if LEARNRHO:
    i.append(rho_inputs)
if LEARNPT:
    i.append(pt_inputs)
pivoter = Model(inputs=i,
                outputs=[y_hat]+kin_hats)
pivoter.compile(optimizer=Adam(lr=0.001),
                loss=['categorical_crossentropy'] + ['categorical_crossentropy' for _ in kin_hats],
                loss_weights=adv_loss_weights)

print '############# ARCHITECTURE #############'
pivoter.summary()
print '###################################'

'''
Now we train both models
'''

if ADV > 0:
    print 'TRAINING ADVERSARIAL NETWORK'
    system('mv logs/train_conv_adv.log logs/train_conv_adv.log.old')
    flog = open('logs/train_conv_adv.log','w')
    callback = LambdaCallback(
        on_batch_end=lambda batch, logs: flog.write('%i,%f,%f,%f,%f\n'%(batch,logs['loss'],logs['dense_6_loss'],logs['dense_7_loss'],logs['dense_1_loss'])),
        on_epoch_end=lambda epoch, logs: save_classifier(name='regularized_conv')
    )

    tb = TensorBoard(
        log_dir = './logs/conv_logs',
        write_graph = True,
        write_images = True
    )

    print '  -Pre-training the classifier'

    # bit of pre-training to get the classifer in the right place 
    classifier.fit_generator(classifier_train_gen, 
                             steps_per_epoch=1000, 
                             epochs=2)


    save_classifier(name='pretrained_conv')

    # np.set_printoptions(threshold='nan')
    # print test_o
    # print classifier.predict(test_i)


    def save_and_exit(signal=None, frame=None, name='regularized_conv', model=classifier):
        save_classifier(name, model)
        flog.close()
        exit(1)
    signal.signal(signal.SIGINT, save_and_exit)

    print '  -Training the adversarial stack'

    # now train the model for real
    pivoter.fit_generator(train_gen, 
                          steps_per_epoch=5000,
                          epochs=NEPOCH*2,
                          callbacks = [ModelCheckpoint('models/regularized_conv_%s_{epoch:02d}_{val_loss:.5f}.h5')],
                          validation_data=validation_gen,
                          validation_steps=100
                         )


    save_classifier(name='regularized_conv')
    save_classifier(name='pivoter_conv', model=pivoter)
    flog.close()

if ADV % 2 == 0:
    print 'TRAINING CLASSIFIER ONLY'

    system('mv logs/train_conv.log logs/train_conv.log.old')
    flog = open('logs/train_conv.log','w')
    callback = LambdaCallback(
        on_batch_end=lambda batch, logs: flog.write('%i,%f\n'%(batch,logs['loss'])),
        on_epoch_end=lambda epoch, logs: save_classifier(name='classifier_conv')
    )

    tb = TensorBoard(
        log_dir = './logs/lstmnoreg_logs',
        write_graph = True,
        write_images = True
    )

    n_epochs = 1 if (ADV == 2) else 2 # fewer epochs if network is pretrained
    n_epochs *= NEPOCH

    def save_and_exit(signal=None, frame=None, name='classifier_conv', model=classifier):
        save_classifier(name, model)
        flog.close()
        exit(1)
    signal.signal(signal.SIGINT, save_and_exit)
    
    classifier.fit_generator(classifier_train_gen, 
                             steps_per_epoch=100, 
                             epochs=n_epochs,
                             callbacks = [ModelCheckpoint('models/classifier_conv_%s_{epoch:02d}_{val_loss:.5f}.h5')],
                             validation_data=classifier_validation_gen,
                             validation_steps=100
                            )


    save_classifier(name='classifier_conv')

