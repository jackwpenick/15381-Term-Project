import pygame
from pygame.locals import *
import neat
from neat import population, config
from neat.graphs import feed_forward_layers
from neat.six_util import itervalues
#from neat.nn.feed_forward import FeedForwardNetwork
# rather than import FeedForwardNetwork, I copy-pasted the class here for modification
# This because the modifications are too deep  to be handled via inheritance
from itertools import cycle
import random as rnd
import time
import math

LEARNING_RATE=0.1
DISCOUNT_FACTOR=0.9

config = config.Config(
        neat.DefaultGenome, neat.DefaultReproduction,
        neat.DefaultSpeciesSet, neat.DefaultStagnation,
        'flappy_config'
    )

def avg(iterable, valifempty=0):
    count = 0
    total = 0
    for x in iterable:
        if x is not None:
            count += 1
            total += x
    return total / count if count != 0 else valifempty

class FeedForwardNetwork(object):
    # Only works with sigmoid activation function
    def __init__(self, inputs, outputs, node_evals):
        self.input_nodes = inputs
        self.output_nodes = outputs
        self.node_evals = node_evals
        self.values = dict((key, 0.0) for key in inputs + outputs)

    def activate(self, inputs):
        if len(self.input_nodes) != len(inputs):
            raise Exception("Expected {0} inputs, got {1}".format(len(self.input_nodes), len(inputs)))

        for k, v in zip(self.input_nodes, inputs):
            self.values[k] = v

        for node, act_func, agg_func, bias, response, links, rev_links in self.node_evals:
            node_inputs = []
            for i, w in links:
                node_inputs.append(self.values[i] * w)
            s = agg_func(node_inputs)
            self.values[node] = act_func(bias + response * s)

        return [self.values[i] for i in self.output_nodes]

    def backpropagated_weight_errors(self, inputs, expected_outputs):
        # expected_outputs may contain Nones, which is OK
        # ignore those outputs and connections connected to those outputs

        # set up the self.values array
        self.activate(inputs)

        y = self.values
        dy = {}
        dx = {}
        dw = {}

        for i, d in zip(self.output_nodes, expected_outputs):
            if d is None:
                dy[i] = None
            else:
                dy[i] = y[i] - d


        for node, _, _, bias, _, links, rev_links in reversed(self.node_evals):
            if node not in self.output_nodes:
                for (i, w) in rev_links:
                    if i not in self.nodes: continue
                    if dx[i] is not None:
                        if node in dy:
                            dy[node] += dx[i] * w
                        else:
                            dy[node] = dx[i] * w
                if node not in dy:
                    dy[node] = None
            else:
                assert node in dy
            if dy[node] is None:
                dx[node] = None
                for (i, w) in rev_links:
                    if i not in self.nodes: continue
                    dw[node, i] = None
                for (i, w) in links:
                    if i in self.input_nodes:
                        # input nodes have no node_eval, so update
                        # the weight derivitives here
                        dw[i, node] = None
            else:
                dx[node] = dy[node] * y[node] * (1 - y[node])
                for (i, w) in rev_links:
                    if i not in self.nodes: continue
                    if dx[i] is None:
                        dw[node, i] = None
                    else:
                        dw[node, i] = y[node] * dx[i]
                for (i, w) in links:
                    if i in self.input_nodes:
                        # input nodes have no node_eval, so update
                        # the weight derivitives here
                        dw[i, node] = dx[node] * y[i]
            dw["bias", node] = dx[node] #* y["bias"]

        return dw

    def backpropagate(self, inputses, expected_outputses):
        weight_errors = []
        for (inputs, expected_outputs) in zip(inputses, expected_outputses):
            weight_errors.append(
                self.backpropagated_weight_errors(
                    inputs, expected_outputs
                )
            )
        avg_weight_errors = {}
        for ne_idx, (node, act_func, agg_func, bias, response, links, rev_links) in enumerate(self.node_evals):
            new_links = []
            for i, w in links:
                avg_weight_errors[i, node] = avg(weight_error[i, node] for weight_error in weight_errors)
                new_links.append((i, w + LEARNING_RATE * avg_weight_errors[i, node]))
            avg_weight_errors["bias", node] = avg(weight_error["bias", node] for weight_error in weight_errors)
            new_bias = bias + LEARNING_RATE * avg_weight_errors["bias", node]
            self.node_evals[ne_idx] = (node, act_func, agg_func, new_bias, response, new_links, rev_links)
            self.genome.nodes[node].bias = new_bias

        for node, _, _, _, _, links, rev_links in self.node_evals:
            new_rev_links = []
            for i, w in rev_links:
                if i not in self.nodes: continue
                new_rev_links.append((i, w + LEARNING_RATE * avg_weight_errors[node, i]))
            for i, L in enumerate(new_rev_links):
                rev_links[i] = L

        for cg in itervalues(self.genome.connections):
            if cg.enabled:
                inode, onode = cg.key
                if (inode, onode) not in avg_weight_errors:
                    assert inode not in self.nodes or onode not in self.nodes
                else:
                    cg.weight += LEARNING_RATE * avg_weight_errors[inode, onode]
        return FeedForwardNetwork.create(self.genome, self.config)

    @staticmethod
    def create(genome, config):
        """ Receives a genome and returns its phenotype (a FeedForwardNetwork). """

        connections = [cg.key for cg in itervalues(genome.connections) if cg.enabled]

        layers = feed_forward_layers(config.genome_config.input_keys, config.genome_config.output_keys, connections)
        node_evals = []
        nodes = set([])
        for layer in layers:
            for node in layer:
                nodes.add(node)
                links = []
                rev_links = []
                node_expr = []
                for cg in itervalues(genome.connections):
                    inode, onode = cg.key
                    if onode == node and cg.enabled:
                        links.append((inode, cg.weight))
                        node_expr.append("v[{}] * {:.7e}".format(inode, cg.weight))
                    elif inode == node and cg.enabled:
                        rev_links.append((onode, cg.weight))

                ng = genome.nodes[node]
                aggregation_function = config.genome_config.aggregation_function_defs[ng.aggregation]
                activation_function = config.genome_config.activation_defs.get(ng.activation)
                node_evals.append((node, activation_function, aggregation_function, ng.bias, ng.response, links, rev_links))

        self = FeedForwardNetwork(config.genome_config.input_keys, config.genome_config.output_keys, node_evals)
        self.config = config
        self.genome = genome
        self.nodes = nodes
        return self



#####################################
# Game parameters
#####################################

FPS, WIDTH, HEIGHT = 30, 288, 512
SPACING = 184 # distance between pipes
GAME_VEL = -4 # velocity of the game

generation, highscore = 0, 0 # statistic counters
BASEX, BASEY = 0, int(0.79 * HEIGHT) # position of ground
GAP = 75 # gap in the pipes

path = 'assets/'
IMAGES, HITMASKS = {}, {}

#####################################
# Bird class
#####################################

class Bird:
    ''' Contains all data about the bird and his neural network '''
    def __init__(self, genome):
        self.genome = genome
        self.color = ['blue', 'red', 'yellow', 'black'][rnd.randint(0,3)]
        self.state = 0 # denotes the state of the wing
        self.generator = cycle([0,1,2,1]) # iterator of the wing states
        self.alive = True
        self.jumps = 0 # counts total number of flaps

        self.x = WIDTH // 5
        self.y = HEIGHT // 2.5

        self.velocity = -8 # vertical velocity
        self.acceleration = 1 # vertical acceleration

        self.experiences = {
            "inputses": [],
            "actions": [], # 0 for flap, 1 for no flap
        }
        self.brain = FeedForwardNetwork.create(genome, config)

    def backpropagate(self, value):
        discount_factors = [
            DISCOUNT_FACTOR ** i for i in range(len(self.experiences["actions"]))
        ]
        expected_outputses = [
            ([None, value * discount_factor]
             if action == 1
             else [value * discount_factor, None])
            for action, discount_factor in zip(
                    self.experiences["actions"], discount_factors
                )
        ]

        inputses = self.experiences["inputses"]

        self.brain = self.brain.backpropagate(inputses, expected_outputses)

        # reset experiences
        self.experiences = {
            "inputses": [],
            "actions": [], # 0 for flap, 1 for no flap
        }

    def image(self):
        ''' called at each tick of the clock '''
        self.state = self.generator.__next__() # move the wing
        self.y = min(self. y + self.velocity, BASEY - 23) # update position
        if self.velocity < 12: # update velocity
            self.velocity += self.acceleration
        return IMAGES[self.color+'-'+str(self.state)]

    def flap(self):
        if self.y < 10: # bird is too high to flap
            return
        self.jumps += 1
        if self.velocity < 0: # midflap flap
            self.velocity = max(-12, self.velocity - 8)
        else: # non-midflap flap
            self.velocity = -8

    def decision(self, pipes, score):
        ''' decides whether to flap or not '''
        # positions of the two leftmost pipes on the screen
        p1x, p1y = pipes[0][1][0], pipes[0][1][1]
        p2x, p2y = pipes[1][1][0], pipes[1][1][1]
        p3x, p3y = pipes[2][1][0], pipes[2][1][1]

        # set normalized velocity as one of the inputs
        inputs = [float(self.velocity + 13) / 25]

        # bird only sees the pipe that is IN FRONT of him,
        # so add the normalized relative position of the pipe to inputs
        if p1x - self.x >= -35:
            inputs += [float(p1x) / WIDTH, (float(p1y) - self.y) / HEIGHT]
        else:
            inputs += [float(p2x) / WIDTH, (float(p2y) - self.y) / HEIGHT]


        action = 1

        # get output of the neural network
        output = self.brain.activate(inputs)
        # and decide
        if output[0] > output[1]:
            self.flap()
            action = 0

        self.experiences["inputses"].append(inputs)
        self.experiences["actions"].append(action)

    def collided(self, pipes):
        ''' finds out whether the bird is in collision right now '''
        if self.y + 24 >= BASEY: # with ground
            return True

        bird_rect = pygame.Rect(self.x, self.y, 34, 24)
        for pipe in pipes: # with pipes
            pipe_up_rect = pygame.Rect(pipe[0][0], pipe[0][1], 52, 320)
            pipe_down_rect = pygame.Rect(pipe[1][0], pipe[1][1], 52, 320)

            if pixelCollision(bird_rect, pipe_up_rect, HITMASKS['bird-'+str(self.state)], HITMASKS['pipe_up']):
                return True
            if pixelCollision(bird_rect, pipe_down_rect, HITMASKS['bird-'+str(self.state)], HITMASKS['pipe_down']):
                return True
        return False

#####################################
# Helper methods
#####################################

def sigmoid(x):
  return 1 / (1 + math.exp(-x))

def get_mask(image):
    ''' returns pixels with zero alpha channel '''
    mask = []
    for i in range(image.get_width()):
        mask.append([])
        for j in range(image.get_height()):
            mask[i].append(bool(image.get_at((i,j))[3]))
    return mask

def random_pipe(x):
    ''' returns coordinates of upper and lower pipes at position x '''
    gapY = rnd.randrange(0, int(BASEY * 0.6 - GAP)) + BASEY // 5
    pipe_height = IMAGES['pipe_down'].get_height()
    return([(x, gapY - pipe_height),(x, gapY + GAP)])

def show_score(score):
    ''' displays score in center of screen '''
    scoreDigits = [int(x) for x in list(str(score))]
    totalWidth = 0
    for digit in scoreDigits:
        totalWidth += IMAGES['numbers'][digit].get_width()
    Xoffset = (WIDTH - totalWidth) / 2
    for digit in scoreDigits:
        SCREEN.blit(IMAGES['numbers'][digit], (Xoffset, HEIGHT // 10))
        Xoffset += IMAGES['numbers'][digit].get_width()

def pixelCollision(rect1, rect2, hitmask1, hitmask2):
    """Checks if two objects collide """
    rect = rect1.clip(rect2)
    if rect.width == 0 or rect.height == 0:
        return False
    x1, y1 = rect.x - rect1.x, rect.y - rect1.y
    x2, y2 = rect.x - rect2.x, rect.y - rect2.y
    for x in range(rect.width):
        for y in range(rect.height):
            if hitmask1[x1+x][y1+y] and hitmask2[x2+x][y2+y]:
                return True
    return False

#####################################
# Game loop for one generation
#####################################

def eval_fitness(genomes, config):
    global BASEX, generation, highscore

    birds = []
    for _, genome in genomes:
        b = Bird(genome) # a bird is born
        birds.append(b)

    birds_alive = len(birds)
    for i in range(len(birds)):
        birds[i].y += 5 * rnd.randint(-5,5)

    # initialize pipe array
    pipes = [random_pipe(WIDTH + 100 + i * SPACING) for i in range(3)]

    # initialize score and time
    score, score_added = 0, False
    start = time.time()

    # play until there is a survivor
    while birds_alive:
        SCREEN.blit(IMAGES['background'], (0, 0))
        for p in pipes:
            SCREEN.blit(IMAGES['pipe_up'], p[0])
            SCREEN.blit(IMAGES['pipe_down'], p[1])
        SCREEN.blit(IMAGES['base'], (BASEX, BASEY))

        # move everything that needs movement
        BASEX = -((-BASEX + 4) % 48)
        for p in pipes:
            p[0] = (p[0][0] + GAME_VEL, p[0][1])
            p[1] = (p[1][0] + GAME_VEL, p[1][1])
        if not score_added and pipes[0][0][0] < WIDTH // 5 - IMAGES['pipe_up'].get_width() // 2:
            score += 1
            score_added = True
            lifespan = float(time.time() - start)
            for b in birds:
                if not b.alive:
                    continue
                b.backpropagate(sigmoid(- b.jumps / 1000.0 + lifespan / 200.0))
        if pipes[0][0][0] < -50: # get rid of the left pipe and add a new one
            pipes = pipes[1:]
            pipes.append(random_pipe(pipes[1][0][0] + SPACING))
            score_added = False

        for b in birds:
            if not b.alive:
                continue
            if b.collided(pipes):
                b.alive = False
                b.final_score = score
                birds_alive -= 1
                highscore = max(highscore, score)
                lifespan = float(time.time() - start)
                b.backpropagate(sigmoid(1 / 10.0 - b.jumps / 1000.0 + lifespan / 200.0))
                # FITNESS FUNCTION
                # takes into account score, # of flaps and lifespan
                b.genome.fitness = sigmoid(score / 10.0 - b.jumps / 1000.0 + lifespan / 200.0)
            b.decision(pipes, score)
            SCREEN.blit(b.image(), (b.x, b.y))

        # print statistics
        label1 = FONT.render('Alive: ' + str(birds_alive), 2, (0,0,0))
        label2 = FONT.render('HIGHSCORE: ' + str(highscore), 2, (0,0,0))
        label3 = FONT.render('GENERATION: ' + str(generation), 2, (0,0,0))
        SCREEN.blit(label1, (20, 440))
        SCREEN.blit(label2, (20, 460))
        SCREEN.blit(label3, (20, 480))
        show_score(score)

        # update the screen and tick the clock
        pygame.display.update()
        FPSCLOCK.tick(FPS)
    generation += 1

#####################################
# Main skeleton
#####################################

def main():
    # initalize game
    global FPSCLOCK, SCREEN, FONT
    pygame.init()
    FONT = pygame.font.SysFont("monospace", 15)
    FPSCLOCK = pygame.time.Clock()
    SCREEN = pygame.display.set_mode((WIDTH, HEIGHT))
    pygame.display.set_caption('Flappy Bird Evolution')

    # get game element images
    day_time = ['day', 'night'][rnd.randint(0,1)]
    IMAGES['background'] = pygame.image.load(path+'background-'+day_time+'.png').convert()
    IMAGES['base'] = pygame.image.load(path+'base.png').convert()
    IMAGES['message'] = pygame.image.load(path+'message.png').convert_alpha()
    IMAGES['pipe_down'] = pygame.image.load(path+'pipe-red.png').convert_alpha()
    IMAGES['pipe_up'] = pygame.transform.rotate(IMAGES['pipe_down'], 180)
    IMAGES['numbers'] = [pygame.image.load(path + str(i) + '.png').convert_alpha() for i in range(10)]
    for color in ['blue', 'red', 'yellow', 'black']:
        for state in range(3):
            IMAGES[color+'-'+str(state)] = pygame.image.load(path+color+'bird-'+str(state)+'.png').convert_alpha()

    # get hitmasks
    for i in range(3):
        HITMASKS['bird-'+str(i)] = get_mask(pygame.image.load(path+'bluebird-'+str(i)+'.png').convert_alpha())
    HITMASKS['pipe_up'] = get_mask(IMAGES['pipe_up'])
    HITMASKS['pipe_down'] = get_mask(IMAGES['pipe_down'])

    # show welcome screen
    SCREEN.blit(IMAGES['background'], (0, 0))
    SCREEN.blit(IMAGES['message'], (45, 45))
    SCREEN.blit(IMAGES['base'], (BASEX, BASEY))
    pygame.display.update()

    # hold until space is pushed
    hold = True
    while hold:
        for event in pygame.event.get():
            if event.type == KEYDOWN and event.key == K_SPACE:
                hold = False

    # start evolution
    pop = population.Population(config)
    pop.run(eval_fitness, 10000)

if __name__ == '__main__':
    main()
