"""Functions for training an agent."""
from __future__ import annotations
import logging

from typing import TYPE_CHECKING

import pycrayon

from agent.config import program_args
from agent.data import dataset_split
from agent.data import game_dataset
from agent.data import loading
from agent.evaluation import action_generator_metrics, plan_metrics
from agent.learning import util
from agent.model.model_wrappers import action_generator_model_wrapper
from agent.model.model_wrappers import create_model_wrapper

if TYPE_CHECKING:
    from typing import List
    from agent.config import model_args
    from agent.config import training_args
    from agent.model.model_wrappers import model_wrapper


SLACK_CHANNEL: str = ''


def train(args: program_args.ProgramArgs) -> None:
    """ Trains the model to generate sequences of actions using direct supervision on action sequences.

    Inputs:
        args (program_args.ProgramArgs): The arguments to training and running the program.
    """
    # Set up the simulator depending on whichever type of simulator is described by the arguments.
    training_arguments: training_args.TrainingArgs = args.get_training_args()

    crayon_client: pycrayon.CrayonClient = pycrayon.CrayonClient(hostname='localhost')
    logging.info('Starting experiment: ' + training_arguments.get_experiment_name())

    # Create a new experiment
    experiment: pycrayon.crayon.CrayonExperiment = crayon_client.create_experiment(
        training_arguments.get_experiment_name())

    if training_arguments.log_with_slack():
        util.send_slack_message(username=training_arguments.get_experiment_name(),
                                message='Starting!',
                                channel=SLACK_CHANNEL)

    # Save the arguments to the specified directory.
    program_args.save_args(args, training_arguments.get_save_directory())

    # Load the data.
    train_dataset = loading.load_data(dataset_split.DatasetSplit.TRAIN, args.get_data_args(), args.get_game_args())
    dev_dataset = loading.load_data(dataset_split.DatasetSplit.DEV, args.get_data_args(), args.get_game_args())

    dataset = game_dataset.GameDataset(train_dataset.get_games(dataset_split.DatasetSplit.TRAIN),
                                       dev_dataset.get_games(dataset_split.DatasetSplit.DEV),
                                       dict(),
                                       args.get_data_args(),
                                       randomly_split_trainval=False,
                                       presaved=True)

    logging.info('Loaded ' + str(len(dataset)) + ' games')

    # Save the validation split in a separate file so it can be reloaded later
    dataset.save_validation_split(training_arguments.get_save_directory())
    task: model_args.Task = args.get_model_args().get_task()

    if args.get_model_args().get_decoder_args().pretrained_plan_predictor():
        # Load the vocabulary if the plan predictor was pretrianed.
        vocab_file: str = \
            '/'.join(args.get_model_args().get_decoder_args().pretrained_plan_predictor_filepath().split('/')[:-1])
        logging.info('Loading vocabulary from ' + vocab_file)
        vocabulary = loading.load_vocabulary(vocab_file)
    else:
        # Otherwise, create and save the vocabulary.
        vocabulary: List[str] = dataset.get_instruction_vocabulary()
        dataset.save_vocabulary(training_arguments.get_save_directory())

    logging.info('Vocabulary contains ' + str(len(vocabulary)) + ' word types')

    model: model_wrapper.ModelWrapper = create_model_wrapper.get_model_wrapper(
        args.get_model_args(), training_arguments, vocabulary,
        load_pretrained=args.get_model_args().get_decoder_args().end_to_end())
    logging.info('Created model:')
    logging.info(model)

    # Run the training part
    best_epoch_filename = model.train_loop(dataset,
                                           args.get_game_args(),
                                           args.get_evaluation_args(),
                                           training_arguments,
                                           experiment)
    model.load(best_epoch_filename)
    if training_arguments.log_with_slack():
        util.send_slack_message(username=training_arguments.get_experiment_name(),
                                message='Model finished training! Best epoch filename: ' + best_epoch_filename,
                                channel=SLACK_CHANNEL)

    if task == model_args.Task.PLAN_PREDICTOR:
        logging.info('Running on dev after training for plan prediction...')
        predictions = plan_metrics.plan_metric_results(model, dataset.get_examples(dataset_split.DatasetSplit.DEV))
        print(predictions)

        if training_arguments.log_with_slack():
            util.send_slack_message(username=training_arguments.get_experiment_name(),
                                    message='Final goal-prediction accuracy: ' + '{0:.2f}'.format(
                                        100. * final_goal_acc) + '%',
                                    channel=SLACK_CHANNEL)

    elif task == model_args.Task.ACTION_GENERATOR:
        logging.info('Running on dev after training for action prediction...')
        assert isinstance(model, action_generator_model_wrapper.ActionGeneratorModelWrapper)
        dict_results = action_generator_metrics.execution_accuracies(model,
                                                                     game_arguments=args.get_game_args(),
                                                                     evaluation_arguments=args.get_evaluation_args(),
                                                                     instruction_examples=list(dataset.get_examples(
                                                                         dataset_split.DatasetSplit.DEV).values()))
        for metric_name, result in dict_results.items():
            if training_arguments.log_with_slack():
                util.send_slack_message(username=training_arguments.get_experiment_name(),
                                        message=str(metric_name) + ' after training: ' + '{0:.2f}'.format(result),
                                        channel=SLACK_CHANNEL)
            logging.info(str(metric_name) + ' after training: ' + '{0:.2f}'.format(result))
