import logging
import os


def setup_logger(name, log_filename='bot.log'):
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    log_file = os.path.join(os.path.dirname(__file__), log_filename)
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger