import pandas as pd
import autokeras as ak
import datetime
import os
import tensorflow as tf
import numpy as np
import json
import socket
import time

# Set TensorFlow log level to error
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'

# Initialize current time
currentTime = datetime.datetime.now().strftime('%m-%d-%Y %H-%M-%S')

project_name = '3D2'

gpus = tf.config.experimental.list_physical_devices('GPU')
if gpus:
  try:
    tf.config.experimental.set_virtual_device_configuration(gpus[0], [tf.config.experimental.VirtualDeviceConfiguration(memory_limit=6144)])
  except RuntimeError as e:
    print(e)

# Define function to get full file path
def get_file_path(*subdirs, filename=None):
    base_path = os.path.dirname(os.path.abspath(__file__))
    full_path = os.path.join(base_path, *subdirs)
    if filename is not None:
        full_path = os.path.join(full_path, filename)
    full_path = full_path.replace("/", "\\")
    return full_path

# Read in the parquet file
train = pd.read_parquet(get_file_path('intraday/5min', filename=f'TRAIN_COMBINED.parquet'))
val = pd.read_parquet(get_file_path(f'intraday/5min', filename=f'VAL_COMBINED.parquet'))
test = pd.read_parquet(get_file_path(f'intraday/5min', filename=f'TEST_COMBINED.parquet'))

# Define the target column
target_col = 'open'

# Get the features and target arrays
x_train = train.drop([target_col], axis=1).values
y_train = train[target_col].values

x_val = val.drop([target_col], axis=1).values
y_val = val[target_col].values

x_test = test.drop([target_col], axis=1).values
y_test= test[target_col].values

stopping_callback = tf.keras.callbacks.EarlyStopping(
    monitor="val_loss",
    patience=30
)
checkpoint_callback = tf.keras.callbacks.ModelCheckpoint(
    filepath=get_file_path('models\\checkpoints', filename=f'{project_name}-{currentTime}.h5'),
    monitor='val_loss',
    save_best_only=False,
    save_weights_only=False,
    verbose=1,
    save_freq='epoch'
)

# Define callbacks list
callbacks = [stopping_callback, checkpoint_callback]

# Initialize the model
def run_model():
    clf = ak.TimeseriesForecaster(
        # max_trials=250,
        lookback=5120,
        project_name=project_name,
        overwrite=False,
        objective='val_loss',
        directory=get_file_path('models'),
        metrics='mape',
        loss='huber_loss'
    )
    return clf

# Set the environment variables
os.environ['TF_CONFIG'] = json.dumps({
    'cluster': {
        'worker': ['192.168.0.183:2222', '192.168.0.223:2223']
    },
    'task': {'type': 'worker', 'index': 0}
})
print("Environment variables set...")

# Create a TensorFlow cluster resolver
cluster_resolver = tf.distribute.cluster_resolver.TFConfigClusterResolver()
print("Cluster resolver created...")

# Define the strategy
strategy = tf.distribute.MultiWorkerMirroredStrategy(cluster_resolver=cluster_resolver)
print("Strategy defined...")

def train_model(strategy):
    # Initialize the AutoKeras model
    clf = run_model()

    # Initialize the model with the strategy
    with strategy.scope():
        clf

    # Print the number of workers
    print("Number of devices: {}".format(strategy.num_replicas_in_sync))

    # Train the AutoKeras model
    clf.fit(x_train, y_train, validation_data=(x_val, y_val), epochs=1, shuffle=False, batch_size=256, callbacks=callbacks)

    # Evaluate the model but if there is an error, clear the session and try again
    try:
        print("Evaluating model...")
        predictions = clf.predict(x_test)
        error = np.mean((np.abs(y_test - predictions) / np.abs(predictions)) * 100)
        print(f"Percentage error: {error:.2f}")
    except:
        print("Error evaluating model, clearing session and trying again...")
        tf.keras.backend.clear_session()
        clf = run_model()
        print("Evaluating model...")
        predictions = clf.predict(x_test)
        error = np.mean((np.abs(y_test - predictions) / np.abs(predictions)) * 100)
        print(f"Percentage error: {error:.2f}")

while True:
    # Check if the second worker is available
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex(('192.168.0.223', 2222))
    if result == 0:
        # Second worker is available, start training the model
        strategy = tf.distribute.MultiWorkerMirroredStrategy(cluster_resolver=cluster_resolver)
        train_model(strategy)
    else:
        # Second worker is not available, wait for 60 seconds and check again
        print("Second worker is not available, waiting...")
        time.sleep(60)