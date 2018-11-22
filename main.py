import argparse
from data_loader import load_debug, load_dummy_fixed_length, load_dummy_variable_length, load_iwslt
from device import select_device, with_cpu, with_gpu
import json
import os
from parse import parse_config
from random import random
from tensorboardX import SummaryWriter
from time import time
import torch
import torch.nn as nn
import torch.optim as optim
from utils import get_or_create_dir


# TODO: visualize attention
# TODO: select a random sentence (only 1) when sampling
# TODO: do not go through all of validation set when evaluating


def main():
    args = parse_arguments()
    config_path = args.config
    debug = args.debug
    with open(config_path, 'r') as f:
        config = json.load(f)
    use_gpu, device, device_idx = select_device()
    parsed_config = parse_config(config, device)
    main_path = os.path.dirname(os.path.realpath(__file__))
    name = parsed_config.get('name')
    writer_path = get_or_create_dir(main_path, f'.logs/{name}')
    SOS_token = '<SOS>'
    EOS_token = '<EOS>'
    if debug:
        train_iter, val_iter, source_language, target_language = load_debug(parsed_config, SOS_token, EOS_token, device)
    else:
        # train_iter, val_iter, source_language, target_language = load_iwslt(parsed_config, SOS_token, EOS_token, device)
        # train_iter, val_iter, source_language, target_language = load_dummy_variable_length(parsed_config, SOS_token, EOS_token, device)
        train_iter, val_iter, source_language, target_language = load_dummy_fixed_length(parsed_config, SOS_token, EOS_token, device)
    parsed_config['source_vocabulary_size'] = len(source_language.itos)
    parsed_config['target_vocabulary_size'] = len(target_language.itos)
    if use_gpu:
        with torch.cuda.device(device_idx):
            encoder, decoder = parsed_config['model']
            encoder = encoder.to(device)
            decoder = decoder.to(device)
            parsed_config['model'] = encoder, decoder
            train(train_iter, val_iter, source_language, target_language, SOS_token, EOS_token, writer_path, parsed_config)
    else:
        train(train_iter, val_iter, source_language, target_language, SOS_token, EOS_token, writer_path, parsed_config)


def str2bool(v):
    if v.lower() in ('yes', 'true', 't', 'y', '1'):
        return True
    elif v.lower() in ('no', 'false', 'f', 'n', '0'):
        return False
    else:
        raise argparse.ArgumentTypeError('Boolean value expected.')


def parse_arguments():
    parser = argparse.ArgumentParser(description='Train machine translation model.')
    parser.add_argument('--config', type=str, nargs='?', default='configs/default.json', help='Path to model configuration.')
    parser.add_argument('--debug', type=str2bool, default=False, const=True, nargs='?', help='Use debug mode.')
    return parser.parse_args()


def train(train_iter, val_iter, source_language, target_language, SOS_token, EOS_token, writer_path, parsed_config):
    SOS = target_language.stoi[SOS_token]
    EOS = target_language.stoi[EOS_token]
    writer_train_path = get_or_create_dir(writer_path, 'train')
    writer_val_path = get_or_create_dir(writer_path, 'val')
    writer_train = SummaryWriter(log_dir=writer_train_path)
    writer_val = SummaryWriter(log_dir=writer_val_path)
    epochs = parsed_config.get('epochs')
    loss_fn = parsed_config.get('loss_fn')
    encoder, decoder = parsed_config['model']
    encoder_optimizer, decoder_optimizer = parsed_config['optimizer']
    training = parsed_config.get('training')
    eval_every = training.get('eval_every')
    sample_every = training.get('sample_every')
    step = 1
    for epoch in range(epochs):
        train_iter.init_epoch()
        val_iter.init_epoch()

        for i, train_pair in enumerate(train_iter):
            loss = train_sentence_pair(encoder, decoder, encoder_optimizer, decoder_optimizer, loss_fn, SOS, EOS, train_pair)

            timestamp = time()
            writer_train.add_scalar('loss', loss, step, timestamp)

            if (i + 1) % eval_every == 0:
                val_losses = 0
                val_lengths = 0
                for val_pair in val_iter:
                    val_loss, _ = evaluate_sentence_pair(encoder, decoder, loss_fn, SOS, EOS, val_pair)
                    val_losses += val_loss
                    val_lengths += 1
                val_loss = val_losses / val_lengths
                writer_val.add_scalar('loss', val_loss, step, timestamp)

            if (i + 1) % sample_every == 0:
                for j, val_pair in enumerate(val_iter):
                    if j == 5:
                        break
                    _, translation = evaluate_sentence_pair(encoder, decoder, loss_fn, SOS, EOS, val_pair)
                    text = get_text(source_language, target_language, val_pair.src, val_pair.trg, translation, SOS_token, EOS_token)
                    writer_val.add_text('translation', text, step, timestamp)

            step += 1


def train_sentence_pair(encoder, decoder, encoder_optimizer, decoder_optimizer, loss_fn, SOS, EOS, pair):
    encoder.train()
    decoder.train()

    source_sentence = pair.src
    target_sentence = pair.trg
    encoder_hidden = encoder.init_hidden()
    source_sentence_length = source_sentence.size(0)
    source_hiddens = with_gpu(torch.zeros(source_sentence_length, encoder.hidden_size))
    target_sentence_length = target_sentence.size(0)

    for i in range(source_sentence_length):
        encoder_output, encoder_hidden = encoder(source_sentence[i], encoder_hidden)
        source_hiddens[i] = encoder_output[0, 0]

    decoder_input = with_gpu(torch.LongTensor([[SOS]]))
    decoder_hidden = encoder_hidden
    context = encoder_hidden[0]

    loss = with_gpu(torch.FloatTensor([0]))
    for i in range(target_sentence_length):
        y, context, decoder_hidden = decoder(source_sentence_length, source_hiddens, decoder_input, context, decoder_hidden)
        context = context.unsqueeze(0)
        topv, topi = y.topk(1)
        decoder_input = topi.detach()
        target = target_sentence[i].view(1)
        loss += loss_fn(y, target)
        if decoder_input.item() == EOS:
            break

    encoder_optimizer.zero_grad()
    decoder_optimizer.zero_grad()
    loss.backward()
    encoder_optimizer.step()
    decoder_optimizer.step()

    return with_cpu(loss)


def evaluate_sentence_pair(encoder, decoder, loss_fn, SOS, EOS, pair):
    with torch.no_grad():
        encoder.eval()
        decoder.eval()

        source_sentence = pair.src
        target_sentence = pair.trg
        encoder_hidden = encoder.init_hidden()
        source_sentence_length = source_sentence.size(0)
        source_hiddens = with_gpu(torch.zeros(source_sentence_length, encoder.hidden_size))
        target_sentence_length = target_sentence.size(0)

        for i in range(source_sentence_length):
            encoder_output, encoder_hidden = encoder(source_sentence[i], encoder_hidden)
            source_hiddens[i] = encoder_output[0, 0]

        decoded_words = []
        decoder_input = with_gpu(torch.LongTensor([[SOS]]))
        decoder_hidden = encoder_hidden
        context = encoder_hidden[0]

        max_length = max(10, 2 * target_sentence_length)
        loss = with_gpu(torch.FloatTensor([0]))
        i = 0
        while True:
            y, context, decoder_hidden = decoder(source_sentence_length, source_hiddens, decoder_input, context, decoder_hidden)
            context = context.unsqueeze(0)
            topv, topi = y.topk(1)
            decoder_input = topi
            decoded_word = topi.item()
            if i < target_sentence_length:
                target = target_sentence[i].view(1)
                loss += loss_fn(y, target).item()
            if decoded_word == EOS:
                break
            decoded_words.append(decoded_word)
            if (i + 1) > max_length:
                break
            i += 1

        return with_cpu(loss), decoded_words


def torch2text(language, sentence, SOS_token, EOS_token):
    sentence = sentence.squeeze()
    sentence = with_cpu(sentence)
    sentence = map(lambda idx: language.itos[idx], sentence)
    sentence = filter(lambda word: word != SOS_token and word != EOS_token, sentence)
    sentence = " ".join(sentence)
    return sentence


def get_text(source_language, target_language, source, target, translation, SOS_token, EOS_token):
    source = torch2text(source_language, source, EOS_token, SOS_token)
    target = torch2text(target_language, target, EOS_token, SOS_token)
    translation = get_sentence(target_language, translation)
    return f"""
    Source: \"{source}\"'
    Target: \"{target}\"
    Translation: \"{translation}\"
    """


def get_sentence(language, words):
    return " ".join(map(lambda word: language.itos[word], words))


if __name__ == '__main__':
    main()
