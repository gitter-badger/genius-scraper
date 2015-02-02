# -*- coding: utf-8 -*-
"""
Created on Mon Jan 19 13:23:46 2015

@author: sunshine
"""
from __future__ import print_function

import json
import re
import time
import traceback
import Queue

from os import mkdir
from hashlib import md5
from collections import defaultdict
from threading import Thread

from tools import *

import requests as rq
from lxml import html

class ThreadLyrics(Thread):
    def __init__(self, queue_in, queue_out ):
        Thread.__init__(self)
        self.qi = queue_in
        self.qo = queue_out

    def regex_blocks(self, regex, blocks, song_artist, features):
        '''
            takes a list of 2-tuples, finds the blocks that match the regular
            expression, then returns those matching blocks as a dictionary
        '''
        artist = None
        formatted_blocks = list()
        del_indeces = list()
        #arbitrary threshold for the amount of characters in the block text
        #until it's considered a match
        length_threshold = 8

        for i, block in enumerate(blocks):
            regex_match = ''
            #main regex match for specified match
            try:
                regex_match = re.search(regex, block[0])
            except Exception as e:
                print(e)
                traceback.print_exc()
            #if primary match was successful and block text is a certain length
            if regex_match and len(block[1]) > length_threshold:
                #defaults the block artist as a song artist
                artist = song_artist
                #checks if any of the featured artists are in the header
                for feature in features:
                    if re.search(an(feature).lower(), an(block[0]).lower()):
                        artist = feature
                if artist == song_artist:
                    if re.search(':', block[0]):
                        stripped = block[0].split(':')[-1].replace(']', '').strip()
                        if stripped:
                            artist = stripped

                #using a hash to compare text blocks
                text_hash = md5(enc_str(block[1])).hexdigest()

                block_dict = {'header': block[0],
                              'text': block[1],
                              'artist': artist,
                              'text hash': text_hash}

                #only adds block if there isn't already one with the same text
                if text_hash not in [block['text hash'] for block in formatted_blocks]:
                    formatted_blocks.append(block_dict)

                #marks block for deletion so futher searches don't get a false
                #positive with similar searches
                del_indeces.append(i)

        for i in del_indeces[::-1]:
            del blocks[i]

        return formatted_blocks

    def run(self):
        while True:
            #grab a lyrics page link and the unprocessed name of the artist
            link, name_raw = self.qi.get()
            attempts = 0
            while attempts < 3:
                try:            
                    response = rq.get(link)
                    break
                except Exception as e:
                    attempts += 1
                    print(e)

            if response.status_code == 200:
                tree = html.fromstring(response.text)
                xpath_query = '//div[@class="lyrics"]//text()'
                results = tree.xpath(xpath_query)

                if len(results) > 10:
                    #raw lyrics text
                    lyrics = ''.join(results)
                    #grab the song name and artist name
                    xq = '//span[@class="text_title"]/text()'
                    try:
                        song_name = tree.xpath(xq)[0].strip()
                    except Exception as e:
                        print(e)
                    xq = '//span[@class="text_artist"]/a/text()'
                    try:
                        name = tree.xpath(xq)[0].strip()
                    except Exception as e:
                        print(e)
 
                    #search for group objects, then return all elements if found
                    ft_group = tree.xpath('//span[@class="featured_artists"]//a/text()')
                    pr_group = tree.xpath('//span[@class="producer_artists"]//a/text()')

                    features = [ft.strip() for ft in ft_group]
                    producers = [pr.strip() for pr in pr_group]
     
                    #dictionary to store all of out raw and processed lyrics data
                    block_dict = {'raw': lyrics, 'pro': dict()}
    
                    #regex to parse block header order, block headers look like 
                    #'[ Verse 1: Gucci Mane ]' or '[Hook]'
                    block_order = re.findall(r'(\[.{4,}(?!\?)\])\n', lyrics)
                    
                    #regex to parse all blocks, except for block references
                    block_regex = r'''
                    (\[.{4,}(?!\?)\]) #match verse headers, but no
                                        #inline question blocks, e.g [???]
                    (?!\n\n)          #lookahead to filter block references out
                    \n*               #filters out the newline trailing block headers
                    ([\w\W\n]*?)      #matches all following chracters, not greedy
                        (?=\n+\[|$)   #lookahead to stop when we hit the next header
                                        #or the end of the lyrics
                    '''
                    blocks = re.findall(block_regex, lyrics, re.VERBOSE)

                    #if no blocks were distinguished, try this more liberal
                    #parenthesis based regex for header matching
                    if not blocks:
                        block_regex = r'''
                        \n{,2}        #leaves out preceeding newlines
                        ([\w\W]*?\(.{4,}(?!\?)\))   #matches headers with parenthesis
                        :?\n*         #leaves out any colons or newlines inbetween
                        ([\w\W\n]*?)  #grabs all the text until the lookahead
                            (?=\n\n|$|\()
                        '''
                        blocks = re.findall(block_regex, lyrics, re.VERBOSE)
                        block_order = re.findall(r'(\(.{4,}(?!\?)\))\n', lyrics)

                    #tries to find the name of the song artist in each header 
                    names_regex = '|'.join((name, name.upper(),
                                            name.title(), name.lower(),
                                            remove_last_word(name.title())))

                    #lambda to preconfigure regex calls
                    artist_regex = (lambda regex:
                                    self.regex_blocks(regex, blocks, name, features))
    
                    #grabs all of the blocks we need based on regex searches
                    #of each of the blocks' headers
                    intro = artist_regex('[iI]ntro')
                    hooks = artist_regex('[hH]ook|[cC]horus')
                    bridge = artist_regex('[bB]ridge')
                    verses = artist_regex('[vV]erse|' + names_regex)
    
                    #match all regex, just to format the raw remainders
                    remainders = artist_regex('([\w\W\n]*?)')

                    #primary entries we need to add even if empty
                    block_dict['pro']['order'] = block_order
                    block_dict['pro']['blocks'] = {'hooks': hooks, 'verses': verses}
                    block_dict['pro']['artist'] = name
                    
                    #these are non essential entries to add if they exist
                    if intro:
                        block_dict['pro']['blocks']['intro'] = intro
                    if remainders:
                        block_dict['pro']['blocks']['remainders'] = remainders
                    if bridge:
                        block_dict['pro']['blocks']['bridge'] = bridge
                    if features:
                        block_dict['pro']['features'] = features
                    if producers:
                        block_dict['pro']['producers'] = producers
                    print('processed lyrics: ' + song_name)
                    self.qo.put((block_dict, song_name, name))
            else:
                print(song_name + ' download failed or aborted')
            #print(song_name + ' task completed')
            self.qi.task_done()


class ThreadPageNameScrape(Thread):
    def __init__(self, queue_in, queue_out):
        Thread.__init__(self)
        self.qi = queue_in
        self.qo = queue_out

    def run(self):
        while True:
            payload = self.qi.get()
            try:
                url = payload['url']
                name = payload['name']
            except KeyError as e:
                print(e)

            headers = {'Content-Type': 'application/x-www-form-urlencoded',
                       'X-Requested-With': 'XMLHttpRequest'}

            song_link_xpath = '//a[@class="song_name work_in_progress   song_link"]/@href'
            song_links = xpath_query_url(url, song_link_xpath, payload=headers)

            if song_links:
                for song in song_links:
                    self.qo.put((song, name))

            self.qi.task_done()


def xpath_query_url(url, xpath_query, payload=dict()):
    headers = {'User-Agent': 'Mozilla/5.0 Gecko/20100101 Firefox/35.0'}
    if payload:
        headers.update(payload)
    try:
        response = rq.get(url, headers=headers)
        #creates an html tree from the data
        tree = html.fromstring(response.text)
        #XPATH query to grab all of the artist urls, then we grab the first
        return tree.xpath(xpath_query)
    except Exception as e:
        print(e)
        return ''


def fetch_verified():
    q = Queue.Queue(maxsize=10)
    pool = thread_pool(q, 10, ThreadFetchVerifiedArtists)
    artists = list()
    page_limit = 222
    for page in range(page_limit):
        q.put((page, artists))
        print('added page: {}/{} into the queue'.format(page, page_limit),
              end='\r')
    
    q.join()
    del pool
    
    return artists

class ThreadFetchVerifiedArtists(Thread):
    def __init__(self, queue_in):
        Thread.__init__(self)
        self.qi = queue_in
    
    def run(self):
        while True:
            base = 'http://genius.com/verified-artists?page='
            query = '//div[@class="user_details"]/a/text()'
            page, artists = self.qi.get()
            try:
                results = xpath_query_url(base + str(page), query)
            except Exception as e:
                print(e)
            if results:
                for artist in results:
                    artists.append(artist)
            self.qi.task_done()

class ThreadFetchArtistID(Thread):
    def __init__(self, queue_in, queue_out):
        Thread.__init__(self)
        self.qi = queue_in
        self.qo = queue_out
    def run(self):
        while True:
            artist = self.qi.get()
            #lets grab some page data so we can get the official artist name
            url = 'http://genius.com/search/artists?q='
            artist_term = artist.replace(' ', '-')
            artist_link_xpath = '//li/a[@class="artist_link"]/@href'
        
            #gonna grab all of the links and take the first result
            artist_links = xpath_query_url(url + artist_term, artist_link_xpath)
            if artist_links:
                #now that we have the artist link we're going to try to get the artist ID
                artist_id_xpath = '//meta[@property="twitter:app:url:iphone"]/@content'
                artist_id_list = xpath_query_url(artist_links[0], artist_id_xpath)
                artist_id_raw = ''
                if artist_id_list:
                    artist_id_raw = artist_id_list[0]
                    #grabs just the number from the returned link
                    artist_id = artist_id_raw.split('artists/')[1]
                    artist_name_corrected = artist_links[0].split('/')[-1]
                    
                    begin = time.time()
                    base = 'http://genius.com'
                    url = ('{}/artists/songs?for_artist_page={}&page=1&pagination=true'
                           .format(base, artist_id))
                    headers = {'Content-Type': 'application/x-www-form-urlencoded',
                               'X-Requested-With': 'XMLHttpRequest'}
        
                    page_link_xpath = '//div[@class="pagination"]//a[not(@class)]/text()'
                    page_nums = xpath_query_url(url, page_link_xpath, payload=headers)
                    if page_nums:
                        page_last = max([int(pagenum) for pagenum in page_nums])
                        for page in range(page_last + 1):
                            url = ('{}/artists/songs?for_artist_page={}&page={}&pagination=true'
                                    .format(base, artist_id, page))
                            self.qo.put({'url': (url), 
                                         'name': artist_name_corrected})             
                    print('finished processing links for ' + artist_name_corrected + ' in: ' + str(time.time() - begin)[:5] + ' seconds')
            self.qi.task_done()

class ThreadWrite(Thread):
    def __init__(self, queue_in):
        Thread.__init__(self)
        self.qi = queue_in

    def run(self):
        while True:
            data, song_name, name = self.qi.get()
 
            if not osp.isdir(ap('lyrics')):
                mkdir(ap('lyrics'))

            path = ap('lyrics/' + name + '.json')
            if not osp.isfile(path):          
                with open(path, 'w+') as fp:
                    lyrics_db = dict()
                    lyrics_db[song_name] = data
                    json.dump(lyrics_db, fp, indent=4)
                    print('first write to: ' + name + ' successful')
            else:
                with open(path, 'r+') as fp:
                    lyrics_db = dict()
                    lyrics_db[song_name] = data
                    jsondata = json.dumps(lyrics_db, indent=4)[2:-1]
                    fp.seek(-2, 2)
                    fp.write(',\n')
                    fp.write(jsondata)
                    fp.write('}')

            self.qi.task_done()


def fetch_artist_song_links(artist_id, name, qi):
    '''
        Grabs the song titles from the paginating calls to the designated
        artist page.  The extra headers are so that the server only gives us 
        the resultant song group and no extranious html.
        
        When you scroll down to the bottom of the songs page in a web browser, 
        it will send a AJAX request to the server to grab a tiny packet of 
        html to insert into the existing results. This emulates that so we 
        can get a much faster response from the server, and reduce data 
        transfer overhead by about 75% per request.
    '''
    


def fetch_lyrics(song_link, name, qi):
    '''
        None of the threads access the same entries at any point in time so
        there aren't any concurrency issues to deal with
    '''
    lyrics_db = defaultdict(defaultdict(dict))

    qi.put((link, name, artist_id))

    if not osp.isdir(ap('lyrics')):
        mkdir(ap('lyrics'))
    with open(ap('lyrics/' + name + '.json'), 'r+') as fp:
        json.dump(lyrics_db, fp, indent=4)

def scrape(artist_names=['Gucci mane']):
    q_id = Queue.Queue()
    q_links = Queue.Queue(maxsize=10)
    q_lyrics = Queue.Queue()
    q_write = Queue.Queue()
    
    pool_id = thread_pool(q_id, 10, ThreadFetchArtistID, qo=q_links)
    pool_links = thread_pool(q_links, 10, ThreadPageNameScrape, qo=q_lyrics)
    pool_lyrics = thread_pool(q_lyrics, 10, ThreadLyrics, qo=q_write)
    pool_write = thread_pool(q_write, 1, ThreadWrite)
    
    for artist in artist_names:
        q_id.put(artist)

    q_id.join()
    del pool_id
    print('finished fetching artist IDs')

    q_links.join()
    del pool_links
    print('finished fetching song links')

    q_lyrics.join()
    del pool_lyrics
    print('finished fetching lyrics')

    q_write.join()
    del pool_write
    print('finished writing lyrics')

def scrape_rapper_list():
    path = ap('rapper-list.json')
    print(path)
    if osp.isfile(path):
        artists = json.load(open(path, 'r+'))
        is_file = lambda name: osp.isfile(ap('lyrics/' + str(name) + '.json'))
        artists = [x for x in artists if not is_file(enc_str(x))]
        scrape(artist_names=artists)
    
    
if __name__ == '__main__':
    if len(sys.argv) > 1:
        scrape(artist_names=[sys.argv[1]])
    else:
        scrape_rapper_list()
